"""Где потолок параллелизма: успех запросов при разной глубине залпа.

Прод в это время держит свои 16 одновременных запросов — значит измеряем ДОБАВКУ сверх
базовых 16. Ищем, с какой глубины пул начинает отказывать (curl 97 = прокси отверг).
"""
import asyncio
import time
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import capture_session_tokens, build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import proxy as proxy_mod

KEYWORDS = ["dentist", "clinic", "loan", "keto", "solar", "vpn", "hotel", "crypto",
            "coffee", "yoga", "roofing", "dating", "shoes", "gym", "pizza", "car"]


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
    """Каждый запрос — своя curl-сессия: имитируем отдельный воркер, а не общий пул сокетов."""
    url = build_library_url(country="CA", keyword=kw, languages=None,
                            active_status="active", media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to="2026-07-08")
    v = _build_variables(t.variables_template, None, f"ramp-{idx}", url)
    fd = _build_form_data(t.as_tokens_dict(), v)
    px = await proxy_mod.get_proxy_url()
    t0 = time.time()
    try:
        async with AsyncSession() as s:
            r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                             headers=hdr(t), impersonate="chrome131", timeout=40,
                             proxies={"http": px, "https": px})
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


async def run():
    t = await capture_session_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print("глубина | ok | пусто | транспорт | лимит | медиана | всего за")
    for depth in (1, 2, 4, 8, 12, 16, 24):
        t0 = time.time()
        res = await asyncio.gather(*[one(t, KEYWORDS[i % len(KEYWORDS)], i) for i in range(depth)])
        dt = time.time() - t0
        k = {}
        for kind, _, _ in res:
            k[kind] = k.get(kind, 0) + 1
        times = sorted(x[1] for x in res)
        ok = k.get("ok", 0)
        print(f"{depth:>7} | {ok:>2} | {k.get('empty', 0):>5} | {k.get('transport', 0):>9} | "
              f"{k.get('ratelimit', 0):>5} | {times[len(times)//2]:>6.1f}s | {dt:.1f}s"
              f"   (успех {ok*100//depth}%, полезных {ok/max(dt,0.01):.1f}/с)")
        await asyncio.sleep(4)

asyncio.run(run())
