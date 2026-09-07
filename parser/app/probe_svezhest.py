"""Отдаёт ли FB объявления за очень свежий день (21 августа, три дня назад).

Дневной сбор держится на том, что при ранжировании relevancy_monthly_grouped FB отдаёт
самое свежее под отсечкой. Если для трёхдневной давности библиотека ещё не наполнилась,
целевой день просто нечего собирать — и это надо знать ДО того, как парк отработает сутки.

Смотрим распределение дат старта в выдаче под разными отсечками.
"""
import asyncio
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import proxy as proxy_mod
from app import token_pool

STRANITS = 8
PROBY = [
    ("отсечка 22.08 (цель 21-е)", "2026-08-22"),
    ("отсечка 20.08 (цель 19-е)", "2026-08-20"),
    ("отсечка 15.08 (цель 14-е)", "2026-08-15"),
    ("отсечка 09.08 (цель 8-е)",  "2026-08-09"),
]


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def sobrat(t, otsechka, slovo):
    url = build_library_url(country="US", keyword=slovo, languages=None,
                            active_status="all", media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to=otsechka)
    cursor, daty, vsego = None, Counter(), 0
    async with AsyncSession() as s:
        for _ in range(STRANITS):
            v = _build_variables(t.variables_template, cursor, "svezh2", url)
            fd = _build_form_data(t.as_tokens_dict(), v)
            r = None
            for _ in range(12):
                px = await proxy_mod.get_proxy_url()
                try:
                    r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                                     headers=hdr(t), impersonate="chrome131", timeout=40,
                                     proxies={"http": px, "https": px})
                    break
                except Exception:
                    await asyncio.sleep(0.3)
            if r is None or r.status_code != 200 or "1675004" in r.text:
                break
            d = _parse_response_json(r.text)
            src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
            for e in src.get("edges") or []:
                for cr in (e.get("node") or {}).get("collated_results") or []:
                    vsego += 1
                    sd = cr.get("start_date")
                    if sd:
                        daty[datetime.fromtimestamp(int(sd), timezone.utc).date().isoformat()] += 1
            pi = src.get("page_info") or {}
            cursor = pi.get("end_cursor")
            if not cursor or not pi.get("has_next_page"):
                break
    return vsego, daty


async def run():
    t = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print(f"США, слово Diabetes, по {STRANITS} страниц на пробу\n")
    for imya, otsechka in PROBY:
        vsego, daty = await sobrat(t, otsechka, "Diabetes")
        cel = (datetime.strptime(otsechka, "%Y-%m-%d").date().toordinal() - 1)
        cel_iso = datetime.fromordinal(cel).date().isoformat()
        v_cel = daty.get(cel_iso, 0)
        top = ", ".join(f"{d}:{n}" for d, n in daty.most_common(4)) or "—"
        print(f"{imya:<28} карточек {vsego:>3} | в целевой день {v_cel:>3} | даты: {top}")

asyncio.run(run())
