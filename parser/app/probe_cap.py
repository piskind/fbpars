"""Что осталось ЗА обрезом: сколько крео теряет потолок в 400 карточек на срез.

По CA: 9 401 срез упёрся в потолок и дал 106 248 крео из 124 тысяч — то есть почти весь
объём принесли срезы, которые мы сами оборвали. Проверяем на живой выдаче: берём слова,
упёршиеся в потолок, гоняем их до 2 000 карточек и считаем новые В ЦЕЛЕВОМ ДНЕ по
позициям — до 400-й карточки и после.
"""
import asyncio
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession
from sqlalchemy import text

from app.browser import build_library_url
from app.db import AsyncSessionLocal
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import proxy as proxy_mod
from app import token_pool

OTSECHKA = "2026-07-08"
POTOLOK = 2000            # докуда гоним
STARYY_KAP = 400          # где обрывали раньше
DEN_OT, DEN_DO = 1783382400, 1783555200


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def slova_v_potolok(limit: int) -> list[str]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            select slovo from slice_events
            where slovo is not null and country = 'CA' and vsego >= 400 and novyh > 0
            group by slovo order by sum(novyh) desc limit :n
        """), {"n": limit})).all()
    return [r[0] for r in rows]


async def sobrat(tokens, url):
    """Возвращает (id в целевом дне до старого капа, после него, всего карточек)."""
    do_kapa, posle_kapa, vsego, cursor = [], [], 0, None
    async with AsyncSession() as s:
        while vsego < POTOLOK:
            v = _build_variables(tokens.variables_template, cursor, "cap", url)
            fd = _build_form_data(tokens.as_tokens_dict(), v)
            r = None
            for _ in range(14):
                px = await proxy_mod.get_proxy_url()
                try:
                    r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                                     headers=hdr(tokens), impersonate="chrome131", timeout=40,
                                     proxies={"http": px, "https": px})
                    break
                except Exception:
                    await asyncio.sleep(0.4)
            if r is None or r.status_code != 200 or "1675004" in r.text:
                break
            d = _parse_response_json(r.text)
            src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
            for e in src.get("edges") or []:
                for cr in (e.get("node") or {}).get("collated_results") or []:
                    vsego += 1
                    sd = cr.get("start_date")
                    if sd and DEN_OT <= int(sd) <= DEN_DO:
                        (do_kapa if vsego <= STARYY_KAP else posle_kapa).append(str(cr.get("ad_archive_id")))
            pi = src.get("page_info") or {}
            cursor = pi.get("end_cursor")
            if not cursor or not pi.get("has_next_page"):
                break
    return do_kapa, posle_kapa, vsego


async def novyh_iz(ids):
    if not ids:
        return 0
    async with AsyncSessionLocal() as s:
        est = (await s.execute(text("select count(*) from ads where library_id = any(:ids)"),
                               {"ids": list(set(ids))})).scalar() or 0
    return len(set(ids)) - est


async def run():
    tokens = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    words = await slova_v_potolok(6)
    print(f"слова, упиравшиеся в потолок: {', '.join(words)}")
    print(f"гоним до {POTOLOK} карточек, старый обрыв был на {STARYY_KAP}\n")
    print("слово      | карточек | новых до 400 | НОВЫХ ПОСЛЕ 400 | всё ещё есть")
    itogo_do = itogo_posle = 0
    for w in words:
        url = build_library_url(country="CA", keyword=w, languages=None,
                                active_status="inactive", media_type="all",
                                sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                                date_to=OTSECHKA)
        do, posle, vsego = await sobrat(tokens, url)
        n_do, n_posle = await novyh_iz(do), await novyh_iz(posle)
        itogo_do += n_do
        itogo_posle += n_posle
        print(f"{w:<10} | {vsego:>8} | {n_do:>12} | {n_posle:>15} | {vsego >= POTOLOK}")
    print(f"\nитого новых: до старого капа {itogo_do}, ЗА ОБРЕЗОМ {itogo_posle}")
    if itogo_do:
        print(f"потолок 400 срезал в {itogo_posle / max(itogo_do, 1):.1f} раза больше, чем отдал")

asyncio.run(run())
