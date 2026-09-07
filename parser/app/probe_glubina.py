"""Сколько НА САМОМ ДЕЛЕ лежит в богатых языковых корпусах CA / 7 июля.

13 языковых срезов упёрлись в потолок 2 000 и дали по 119 новых каждый — то есть мы их
снова обрезали. Вопрос «дотянем ли до 280 тысяч» упирается ровно в это: если корпус
кончается на трёх тысячах — потолок близко; если тянется на десятки тысяч — объём есть,
просто мы его не берём.

Гоняем три сильнейших языка вглубь и смотрим, где кончается выдача и как долго
продолжают попадаться новые.
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
POTOLOK = 6000
DEN_OT, DEN_DO = 1783382400, 1783555200
SREZY = [("zh", "image"), ("ar", "image"), ("pt", "image")]


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def novyh_iz(ids):
    if not ids:
        return 0
    async with AsyncSessionLocal() as s:
        est = (await s.execute(text("select count(*) from ads where library_id = any(:ids)"),
                               {"ids": list(set(ids))})).scalar() or 0
    return len(set(ids)) - est


async def kopat(tokens, lang, media):
    url = build_library_url(country="CA", keyword=None, languages=[lang],
                            active_status="inactive", media_type=media,
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to=OTSECHKA)
    cursor, vsego, v_dne = None, 0, []
    vehi = []          # (карточек, новых в дне на этот момент)
    ischerpan = False
    async with AsyncSession() as s:
        while vsego < POTOLOK:
            v = _build_variables(tokens.variables_template, cursor, f"glub-{lang}", url)
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
                    await asyncio.sleep(0.3)
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
            if vsego and vsego % 1000 < 10:
                vehi.append((vsego, await novyh_iz(v_dne)))
            pi = src.get("page_info") or {}
            cursor = pi.get("end_cursor")
            if not cursor or not pi.get("has_next_page"):
                ischerpan = True
                break
    return vsego, await novyh_iz(v_dne), len(v_dne), ischerpan, vehi


async def run():
    tokens = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print(f"копаем до {POTOLOK} карточек на срез (наш прод-потолок был 2 000)\n")
    itogi = await asyncio.gather(*[kopat(tokens, l, m) for l, m in SREZY])
    for (lang, media), (vsego, novyh, v_dne, isch, vehi) in zip(SREZY, itogi):
        print(f"--- {lang} / {media}")
        print(f"    карточек прошли: {vsego}, в целевом дне {v_dne}, НОВЫХ {novyh}, "
              f"корпус исчерпан: {isch}")
        if vehi:
            print("    накопление новых: " + ", ".join(f"{k}→{n}" for k, n in vehi))
    print(f"\nвсего новых за пробу: {sum(x[1] for x in itogi)}")

asyncio.run(run())
