"""Что FB реально отдаёт по слову auto/car — и почему срез закрывается на 200-400 карточках.

Ограничения сняты (ранняя остановка выключена, потолок миллион), но срезы всё равно
обрываются. Значит выдачу закрывает сам FB. Смотрим по страницам: сколько карточек,
что с has_next, какие даты, и не приходит ли пустой ответ (тихий троттлинг).
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

OTSECHKA = "2026-08-22"
CEL = "2026-08-21"
STRANITS = 60
PROBY = [("auto", "inactive"), ("auto", "active"), ("car", "active"), ("Diabetes", "active")]


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def projti(t, slovo, status):
    url = build_library_url(country="US", keyword=slovo, languages=None,
                            active_status=status, media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to=OTSECHKA)
    cursor, vsego, daty, pustyh = None, 0, Counter(), 0
    konec_prichina = "дошли до предела пробы"
    stranic = 0
    async with AsyncSession() as s:
        for _ in range(STRANITS):
            v = _build_variables(t.variables_template, cursor, "auto", url)
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
            if r is None:
                konec_prichina = "транспорт не прошёл"; break
            if "1675004" in r.text:
                konec_prichina = "rate limit 1675004"; break
            d = _parse_response_json(r.text)
            src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
            edges = src.get("edges") or []
            stranic += 1
            if not edges:
                pustyh += 1
            for e in edges:
                for c in (e.get("node") or {}).get("collated_results") or []:
                    vsego += 1
                    sd = c.get("start_date")
                    if sd:
                        daty[datetime.fromtimestamp(int(sd), timezone.utc).date().isoformat()] += 1
            pi = src.get("page_info") or {}
            cursor = pi.get("end_cursor")
            if not pi.get("has_next_page"):
                konec_prichina = "FB сказал has_next=false"; break
            if not cursor:
                konec_prichina = "FB не дал курсор"; break
    return vsego, stranic, pustyh, daty, konec_prichina


async def run():
    t = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print(f"США, отсечка {OTSECHKA}, целевой день {CEL}, до {STRANITS} страниц\n")
    for slovo, status in PROBY:
        vsego, stranic, pustyh, daty, prichina = await projti(t, slovo, status)
        v_cel = daty.get(CEL, 0)
        top = ", ".join(f"{d}:{n}" for d, n in daty.most_common(3)) or "—"
        print(f"{slovo:<10} {status:<9} страниц {stranic:>3} | карточек {vsego:>4} | "
              f"в целевом дне {v_cel:>4} | пустых ответов {pustyh}")
        print(f"{'':21}остановились: {prichina} | даты: {top}")

asyncio.run(run())
