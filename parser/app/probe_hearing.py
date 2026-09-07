"""Почему по слову hearing собрались единицы, когда в библиотеке их десятки тысяч.

Гипотеза: ранняя остановка. Воркер прекращает листать, когда ВСЯ пачка карточек оказалась
за пределами целевого дня (_DayDone). Правило выведено из того, что FB отдаёт выдачу по
убыванию свежести. Но ранжирование relevancy_monthly_grouped — не строго по дате: у
крупного слова сверху могут стоять старые «релевантные» объявления, и тогда мы бросаем
срез на первой же пачке, не дойдя до целевого дня.

Смотрим распределение дат по страницам: если 21 августа появляется НЕ на первых страницах,
гипотеза верна.
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
STRANITS = 40
SLOVA = ["Hearing", "Vision", "remedies"]


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
    cursor, po_stranicam, vsego, daty = None, [], 0, Counter()
    async with AsyncSession() as s:
        for nomer in range(STRANITS):
            v = _build_variables(t.variables_template, cursor, "hear", url)
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
            v_stranice = 0
            for e in src.get("edges") or []:
                for cr in (e.get("node") or {}).get("collated_results") or []:
                    vsego += 1
                    sd = cr.get("start_date")
                    if sd:
                        den = datetime.fromtimestamp(int(sd), timezone.utc).date().isoformat()
                        daty[den] += 1
                        if den == CEL:
                            v_stranice += 1
            po_stranicam.append(v_stranice)
            pi = src.get("page_info") or {}
            cursor = pi.get("end_cursor")
            if not cursor or not pi.get("has_next_page"):
                break
    return vsego, po_stranicam, daty


async def run():
    t = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print(f"США, отсечка {OTSECHKA}, целевой день {CEL}, до {STRANITS} страниц\n")
    for slovo in SLOVA:
        for status in ("inactive", "active"):
            vsego, po_stranicam, daty = await projti(t, slovo, status)
            v_cel = daty.get(CEL, 0)
            # на какой странице впервые встретился целевой день
            pervaya = next((i + 1 for i, n in enumerate(po_stranicam) if n), None)
            top = ", ".join(f"{d}:{n}" for d, n in daty.most_common(3)) or "—"
            print(f"{slovo:<10} {status:<9} карточек {vsego:>4} | в целевой день {v_cel:>4} | "
                  f"первая страница с целевым днём: {pervaya} | топ дат: {top}")
            print(f"{'':21}по страницам: {po_stranicam[:20]}")

asyncio.run(run())
