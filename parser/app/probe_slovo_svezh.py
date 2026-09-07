"""Почему словесные срезы пусты на свежем дне, а языковые — нет.

Языковой срез по 8 августа отдаёт ~94 карточки и 19 новых крео, словесный — 1 карточку.
Смотрим, что именно приходит: сколько карточек, какие у них даты старта и как отличается
поведение с медиа-фильтром и без него.
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

OTSECHKA = "2026-08-09"      # целевой день 8 августа + 1
STRANITS = 6

SREZY = [
    {"name": "язык zh × image", "languages": ["zh"], "media_type": "image"},
    {"name": "слово funded × image", "keyword": "funded", "media_type": "image"},
    {"name": "слово funded, без медиа", "keyword": "funded", "media_type": "all"},
    {"name": "слово funded, без медиа, статус all", "keyword": "funded",
     "media_type": "all", "active_status": "all"},
    {"name": "слово dentist × image", "keyword": "dentist", "media_type": "image"},
    {"name": "без слова, без языка, image", "media_type": "image"},
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


async def probovat(t, srez):
    url = build_library_url(
        country="CA", keyword=srez.get("keyword"), languages=srez.get("languages"),
        active_status=srez.get("active_status", "inactive"),
        media_type=srez.get("media_type", "all"),
        sort_mode="relevancy_monthly_grouped", sort_direction="desc",
        date_to=OTSECHKA)
    cursor, vsego, daty = None, 0, Counter()
    async with AsyncSession() as s:
        for _ in range(STRANITS):
            v = _build_variables(t.variables_template, cursor, "svezh", url)
            fd = _build_form_data(t.as_tokens_dict(), v)
            r = None
            for _ in range(10):
                px = await proxy_mod.get_proxy_url()
                try:
                    r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                                     headers=hdr(t), impersonate="chrome131", timeout=40,
                                     proxies={"http": px, "https": px})
                    break
                except Exception:
                    await asyncio.sleep(0.3)
            if r is None:
                print(f"    [{srez['name']}] транспорт не прошёл"); break
            if "1675004" in r.text:
                print(f"    [{srez['name']}] rate limit 1675004"); break
            if r.status_code != 200:
                print(f"    [{srez['name']}] HTTP {r.status_code}: {r.text[:80]}"); break
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
    print(f"CA, отсечка {OTSECHKA} (целевой день 8 августа), по {STRANITS} страниц\n")
    for srez in SREZY:
        vsego, daty = await probovat(t, srez)
        top = ", ".join(f"{d}:{n}" for d, n in daty.most_common(4)) or "—"
        print(f"{srez['name']:<38} карточек {vsego:>4} | даты: {top}")

asyncio.run(run())
