"""Нагрузочный гейт: выдержит ли ОДНА сессия параллельные запросы со всех воркеров.

Пул из 6 сессий на 32 воркера = ~5 одновременных запросов на сессию, но всплески бывают
глубже. Стреляем 40 параллельных запросов одной сессией и смотрим на долю 1675004.
"""
import asyncio
import sys
import time
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import capture_session_tokens, build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app import proxy as proxy_mod
from app.graphql_paginator import _build_variables

KEYWORDS = ["dentist", "clinic", "insurance", "loan", "keto", "solar", "casino", "vpn",
            "hotel", "flight", "crypto", "coffee", "yoga", "roofing", "dating", "shoes",
            "watch", "gym", "pizza", "car"]


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def one(s, t, kw, idx):
    url = build_library_url(country="CA", keyword=kw, languages=None,
                            active_status="active", media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to="2026-07-08")
    v = _build_variables(t.variables_template, None, f"load-{idx}", url)
    fd = _build_form_data(t.as_tokens_dict(), v)
    px = await proxy_mod.get_proxy_url()
    t0 = time.time()
    try:
        r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                         headers=hdr(t), impersonate="chrome131", timeout=40,
                         proxies={"http": px, "https": px})
    except Exception as exc:
        return "transport", time.time() - t0, str(exc)[:40]
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
    burst = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    t = await capture_session_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print(f"сессия захвачена, залп {burst} параллельных запросов одной сессией")
    async with AsyncSession() as s:
        for wave in range(3):
            t0 = time.time()
            res = await asyncio.gather(*[one(s, t, KEYWORDS[i % len(KEYWORDS)], i)
                                         for i in range(burst)])
            dt = time.time() - t0
            kinds = {}
            errs = {}
            for k, _, extra in res:
                kinds[k] = kinds.get(k, 0) + 1
                if k == "transport":
                    errs[str(extra)[:45]] = errs.get(str(extra)[:45], 0) + 1
            times = sorted(x[1] for x in res)
            print(f"  волна {wave+1}: {kinds}  за {dt:.1f}s  "
                  f"median={times[len(times)//2]:.1f}s p90={times[int(len(times)*0.9)]:.1f}s")
            for e, c in sorted(errs.items(), key=lambda x: -x[1])[:3]:
                print(f"      транспорт x{c}: {e}")
            await asyncio.sleep(3)
    print(f"возраст сессии на конец теста: {time.time() - t.captured_at:.0f}s")

asyncio.run(run())
