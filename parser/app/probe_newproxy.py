"""Проверка нового мобильного канала боевым способом: curl_cffi + отпечаток Chrome.

Плейн-curl получает от FB 403 — это ожидаемо, он не похож на браузер. Значение имеет
только то, как ходит сам парсер: TLS-отпечаток Chrome и заголовки как у AdLibrary.
"""
import asyncio
import sys
import time
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import token_pool

NOVYY = "socks5h://bmusproxy212147:I97e8T0Qa70x@rn4x7bneaz.cn.fxdx.in:18440"
NOVYY_HTTP = "http://bmusproxy212142:0mxfYaTjy3In@rn4x7bneaz.cn.fxdx.in:18272"

URL = build_library_url(country="CA", keyword="dentist", languages=None,
                        active_status="inactive", media_type="all",
                        sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                        date_to="2026-07-08")


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def zapros(t, proxy, idx):
    v = _build_variables(t.variables_template, None, f"newproxy-{idx}", URL)
    fd = _build_form_data(t.as_tokens_dict(), v)
    t0 = time.time()
    try:
        async with AsyncSession() as s:
            r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                             headers=hdr(t), impersonate="chrome131", timeout=40,
                             proxies={"http": proxy, "https": proxy})
    except Exception as exc:
        return "transport", time.time() - t0, str(exc)[:50]
    dt = time.time() - t0
    if "1675004" in r.text:
        return "ratelimit", dt, ""
    if r.status_code != 200:
        return f"http{r.status_code}", dt, r.text[:60]
    d = _parse_response_json(r.text)
    src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
    n = sum(len((e.get("node") or {}).get("collated_results") or []) for e in src.get("edges") or [])
    return ("ok" if n else "empty"), dt, n


async def run():
    t = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    for imya, proxy in (("socks5", NOVYY), ("http", NOVYY_HTTP)):
        for glubina in (1, 8, 16):
            rez = await asyncio.gather(*[zapros(t, proxy, i) for i in range(glubina)])
            k = {}
            for kind, _, _ in rez:
                k[kind] = k.get(kind, 0) + 1
            times = sorted(x[1] for x in rez)
            oshibka = next((x[2] for x in rez if x[0] not in ("ok", "empty")), "")
            print(f"{imya:<7} глубина {glubina:>2}: {k}  медиана {times[len(times)//2]:.1f}s"
                  + (f"  | {oshibka}" if oshibka else ""))
            await asyncio.sleep(2)

asyncio.run(run())
