"""Годится ли прямое подключение (IP сервера) как ВТОРАЯ полоса для пагинации.

Резидентский пул упёрся в лимит потоков аккаунта: любая добавка параллелизма
превращается в curl 97. Если FB спокойно отдаёт выдачу с серверного IP, это независимая
полоса — её можно нагрузить, не трогая пул.
"""
import asyncio
import sys
import time
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import capture_session_tokens, build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables

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
    url = build_library_url(country="CA", keyword=kw, languages=None,
                            active_status="active", media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to="2026-07-08")
    v = _build_variables(t.variables_template, None, f"direct-{idx}", url)
    fd = _build_form_data(t.as_tokens_dict(), v)
    t0 = time.time()
    try:
        from app.proxy import worker_gateway
        gw = worker_gateway()
        async with AsyncSession() as s:  # мобильный канал gost
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


async def run():
    t = await capture_session_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print("МОБИЛЬНЫЙ канал gost")
    print("глубина | ok | транспорт | лимит | медиана | всего | стр/с")
    for depth in (1, 2, 4, 8, 12):
        t0 = time.time()
        res = await asyncio.gather(*[one(t, KEYWORDS[i % len(KEYWORDS)], i) for i in range(depth)])
        dt = time.time() - t0
        k = {}
        for kind, _, _ in res:
            k[kind] = k.get(kind, 0) + 1
        times = sorted(x[1] for x in res)
        ok = k.get("ok", 0)
        print(f"{depth:>7} | {ok:>2} | {k.get('transport', 0):>9} | {k.get('ratelimit', 0):>5} | "
              f"{times[len(times)//2]:>6.1f}s | {dt:>4.1f}s | {ok/max(dt, .01):.1f}")
        await asyncio.sleep(3)

asyncio.run(run())
