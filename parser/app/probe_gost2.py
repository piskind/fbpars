"""Потолок мобильного канала gost как ВТОРОЙ полосы пагинации.

Залпом канал держит 12 параллельных без ошибок. Но лимиты FB копятся, поэтому здесь:
(1) ramp по глубине, (2) устойчивый прогон на 90 с — важно именно он.
"""
import asyncio
import sys
import time
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import capture_session_tokens, build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app.proxy import worker_gateway

KEYWORDS = ["dentist", "clinic", "loan", "keto", "solar", "vpn", "hotel", "crypto",
            "coffee", "yoga", "roofing", "dating", "shoes", "gym", "pizza", "car",
            "insurance", "casino", "flight", "watch"]


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def one(t, kw, idx):
    url = build_library_url(country="CA", keyword=kw, languages=None,
                            active_status="active", media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to="2026-07-08")
    v = _build_variables(t.variables_template, None, f"g2-{idx}", url)
    fd = _build_form_data(t.as_tokens_dict(), v)
    gw = worker_gateway()
    t0 = time.time()
    try:
        async with AsyncSession() as s:
            r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                             headers=hdr(t), impersonate="chrome131", timeout=40,
                             proxies={"http": gw, "https": gw})
    except Exception as exc:
        return "transport", time.time() - t0, str(exc)[:38]
    dt = time.time() - t0
    if "1675004" in r.text:
        return "ratelimit", dt, ""
    if r.status_code != 200:
        return f"http{r.status_code}", dt, ""
    d = _parse_response_json(r.text)
    src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
    n = sum(len((e.get("node") or {}).get("collated_results") or []) for e in src.get("edges") or [])
    return ("ok" if n else "empty"), dt, n


async def burst(t, depth, tag=""):
    t0 = time.time()
    res = await asyncio.gather(*[one(t, KEYWORDS[i % len(KEYWORDS)], i) for i in range(depth)])
    dt = time.time() - t0
    k = {}
    for kind, _, _ in res:
        k[kind] = k.get(kind, 0) + 1
    times = sorted(x[1] for x in res)
    ok = k.get("ok", 0)
    print(f"{tag}{depth:>4} | {ok:>3} | {k.get('transport', 0):>4} | {k.get('ratelimit', 0):>5} | "
          f"{times[len(times)//2]:>6.1f}s | {ok/max(dt, .01):>5.1f} стр/с")
    return ok, k.get("ratelimit", 0)


async def run():
    t = await capture_session_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print("глуб | ok | трансп | лимит | медиана | темп")
    for depth in (16, 24, 32, 48):
        await burst(t, depth)
        await asyncio.sleep(3)

    # Устойчивый прогон: лимиты FB копятся, залп их не показывает.
    depth = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    print(f"\nустойчивый прогон 90 с при глубине {depth}:")
    stop = time.time() + 90
    tot_ok = tot_rl = tot_tr = 0
    idx = 0
    while time.time() < stop:
        res = await asyncio.gather(*[one(t, KEYWORDS[(idx + i) % len(KEYWORDS)], idx + i)
                                     for i in range(depth)])
        idx += depth
        for kind, _, _ in res:
            if kind == "ok":
                tot_ok += 1
            elif kind == "ratelimit":
                tot_rl += 1
            elif kind == "transport":
                tot_tr += 1
    print(f"  успешных {tot_ok}, лимитов {tot_rl}, транспорт {tot_tr} за 90 с "
          f"→ {tot_ok/90:.1f} стр/с (прод сейчас ~2.0 стр/с всеми 16 воркерами)")

asyncio.run(run())
