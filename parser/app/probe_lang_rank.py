"""Ранжирование языков для CA / 7 июля по ЕДИНСТВЕННОЙ важной метрике:
новые крео, попадающие в целевой день (остальные воркер отбрасывает — only_day).

Гоняем один медиа-срез (inactive/image) на каждый язык: он самый ёмкий и его хватает,
чтобы отсортировать языки. Победителей потом раскатываем по media×status.
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
STRANITS = 8
# границы целевого дня в unix-секундах: FB отдаёт старт как 07:00 UTC (полночь по тихоокеанскому)
DEN_OT, DEN_DO = 1783382400, 1783555200

LANGS = ["zh", "ar", "tr", "pt", "fr", "es", "vi", "hi", "th", "ko", "ja", "id", "tl",
         "fa", "he", "el", "uk", "pl", "nl", "sv", "de", "it", "ru", "en"]


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def sobrat(tokens, url):
    """Возвращает (id_в_целевом_дне, всего_карточек, сбоев, есть_ли_ещё)."""
    v_dne, vsego, sboev, cursor, has_next = [], 0, 0, None, True
    async with AsyncSession() as s:
        for _ in range(STRANITS):
            v = _build_variables(tokens.variables_template, cursor, "rank", url)
            fd = _build_form_data(tokens.as_tokens_dict(), v)
            r = None
            for _try in range(14):
                px = await proxy_mod.get_proxy_url()
                try:
                    r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                                     headers=hdr(tokens), impersonate="chrome131", timeout=40,
                                     proxies={"http": px, "https": px})
                    break
                except Exception:
                    sboev += 1
                    await asyncio.sleep(0.5)
            if r is None or r.status_code != 200 or "1675004" in r.text:
                break
            d = _parse_response_json(r.text)
            src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
            for e in src.get("edges") or []:
                for cr in (e.get("node") or {}).get("collated_results") or []:
                    vsego += 1
                    sd = cr.get("start_date")
                    if sd and DEN_OT <= int(sd) <= DEN_DO:
                        v_dne.append(str(cr.get("ad_archive_id")))
            pi = src.get("page_info") or {}
            cursor = pi.get("end_cursor")
            has_next = bool(pi.get("has_next_page"))
            if not cursor or not has_next:
                break
    return v_dne, vsego, sboev, has_next


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
    print(f"CA / 7 июля, срез inactive+image, по {STRANITS} стр. на язык\n")
    print("язык | карточек | в целевом дне | НОВЫХ В ДНЕ | сбоев | ещё есть")
    itogi = []
    for lang in LANGS:
        url = build_library_url(country="CA", keyword=None, languages=[lang],
                                active_status="inactive", media_type="image",
                                sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                                date_to=OTSECHKA)
        v_dne, vsego, sboev, has_next = await sobrat(tokens, url)
        novyh = await novyh_iz(v_dne)
        itogi.append((novyh, lang, vsego, len(v_dne), has_next))
        print(f"{lang:>4} | {vsego:>8} | {len(v_dne):>13} | {novyh:>11} | {sboev:>5} | {has_next}")
    print("\nпо убыванию отдачи:")
    for novyh, lang, vsego, v_dne, has_next in sorted(itogi, reverse=True):
        if novyh:
            print(f"  {lang}: {novyh} новых в дне из {v_dne} (карточек {vsego}, ещё есть: {has_next})")
    print(f"\nсуммарно новых в дне за зонд: {sum(x[0] for x in itogi)}")

asyncio.run(run())
