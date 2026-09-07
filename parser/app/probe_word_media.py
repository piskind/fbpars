"""Зонд оси «слово × медиа» на CA / 7 июля.

Конспект 17.08 мерил её на США: слово+медиа давало 130-148 новых на срез против 104-135
у чистого слова — множитель ×3, потому что медиа-типы не пересекаются, а базовый срез
обрезается раньше, чем до них доходит. На Канаде ось не проверялась, а поставить её
вслепую дорого: 1 746 результативных слов × 3 медиа = больше 10 тысяч срезов.

Берём слова, уже давшие отдачу по CA, и смотрим, сколько НОВОГО в целевом дне приносит
каждый медиа-срез сверх уже собранного чистым словом.
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
DEN_OT, DEN_DO = 1783382400, 1783555200
SLOVA = ["funded", "trends", "insights", "toyota", "canada", "styles"]
MEDIA = [None, "image", "video", "meme"]   # None = как собирали (без медиа-фильтра)


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
    v_dne, vsego, cursor = [], 0, None
    async with AsyncSession() as s:
        for _ in range(STRANITS):
            v = _build_variables(tokens.variables_template, cursor, "wm", url)
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
            if not cursor or not pi.get("has_next_page"):
                break
    return v_dne, vsego


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
    print(f"CA / 7 июля, inactive, по {STRANITS} стр.\n")
    print("слово      | медиа | карточек | в дне | НОВЫХ В ДНЕ")
    itog = {}
    for slovo in SLOVA:
        for mt in MEDIA:
            url = build_library_url(country="CA", keyword=slovo, languages=None,
                                    active_status="inactive", media_type=mt or "all",
                                    sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                                    date_to=OTSECHKA)
            v_dne, vsego = await sobrat(tokens, url)
            novyh = await novyh_iz(v_dne)
            itog[mt] = itog.get(mt, 0) + novyh
            print(f"{slovo:<10} | {(mt or 'все'):<5} | {vsego:>8} | {len(v_dne):>5} | {novyh:>11}")
    print("\nсуммарно новых в дне по типу среза:")
    for mt in MEDIA:
        print(f"  {(mt or 'все (как собирали)'):<20}: {itog.get(mt, 0)}")

asyncio.run(run())
