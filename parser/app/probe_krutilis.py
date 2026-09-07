"""Сколько объявлений КРУТИЛОСЬ в целевой день против СТАРТОВАВШИХ в этот день.

Заказчик считает первое, мы собираем второе — отсюда «50 тысяч у него, 278 у нас».
FB отдаёт в карточке и start_date, и end_date, поэтому «крутилось 21 августа» считается
точно: старт <= 21.08 И (ещё активно ИЛИ остановлено >= 21.08).

Меряем долю таких карточек в обычной выдаче — она и есть множитель к нынешнему сбору.
"""
import asyncio
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import proxy as proxy_mod
from app import token_pool

DEN = date(2026, 8, 21)
OTSECHKA = "2026-08-22"
STRANITS = 25
SLOVA = ["Hearing", "Diabetes", "Joints"]


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


def krutilos(c) -> bool:
    st = c.get("start_date")
    if not st:
        return False
    if datetime.fromtimestamp(int(st), timezone.utc).date() > DEN:
        return False
    if c.get("is_active"):
        return True
    en = c.get("end_date")
    if not en:
        return False
    return datetime.fromtimestamp(int(en), timezone.utc).date() >= DEN


async def projti(t, slovo, status):
    url = build_library_url(country="US", keyword=slovo, languages=None,
                            active_status=status, media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to=OTSECHKA)
    cursor, vsego, startovali, krutilis = None, 0, 0, 0
    async with AsyncSession() as s:
        for _ in range(STRANITS):
            v = _build_variables(t.variables_template, cursor, "krut", url)
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
                for c in (e.get("node") or {}).get("collated_results") or []:
                    vsego += 1
                    st = c.get("start_date")
                    if st and datetime.fromtimestamp(int(st), timezone.utc).date() == DEN:
                        startovali += 1
                    if krutilos(c):
                        krutilis += 1
            pi = src.get("page_info") or {}
            cursor = pi.get("end_cursor")
            if not cursor or not pi.get("has_next_page"):
                break
    return vsego, startovali, krutilis


async def run():
    t = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print(f"США, целевой день {DEN}, до {STRANITS} страниц на срез\n")
    print(f"{'слово':<10} {'статус':<9} карточек  стартовали в день  КРУТИЛИСЬ в день  множитель")
    itogo_s = itogo_k = 0
    for slovo in SLOVA:
        for status in ("active", "inactive"):
            vsego, st, kr = await projti(t, slovo, status)
            itogo_s += st
            itogo_k += kr
            mn = f"×{kr/st:.1f}" if st else ("—" if not kr else "∞")
            print(f"{slovo:<10} {status:<9} {vsego:>8}  {st:>17}  {kr:>16}  {mn:>9}")
    print(f"\nвсего: стартовали {itogo_s}, крутились {itogo_k}"
          + (f" → множитель ×{itogo_k/itogo_s:.1f}" if itogo_s else ""))

asyncio.run(run())
