"""Зонд по осям для CA / 7 июля: даст ли языковая сетка НОВЫЕ крео.

Словарь по Канаде выбран (76.9k срезов из 80.3k), приток упал до ~130 крео/час. Прежде чем
ставить сетку в 150+ срезов, проверяем на живой выдаче: сколько из возвращённых объявлений
ЕЩЁ НЕ В БАЗЕ. Память прямо предупреждает — ось вслепую не ставить (языковую сетку однажды
поставили на 1712 срезов, 11 воркеров час работали в пустоту).

Ничего не сохраняет: только читает выдачу и сверяет library_id с базой.
"""
import asyncio
import sys
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession
from sqlalchemy import text

from app.browser import build_library_url
from app.db import AsyncSessionLocal
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import proxy as proxy_mod
from app import token_pool

DEN = "2026-07-07"
OTSECHKA = "2026-07-08"   # отсечка = целевой день + 1
STRANITS = 10             # 10 страниц = 100 карточек на срез

# (язык, статус, медиа) — None в языке = контрольный срез без языкового фильтра
PLAN = [
    ("en", "inactive", "image"),
    ("en", "inactive", "video"),
    ("ru", "inactive", "image"),
    ("hi", "inactive", "image"),
    ("tr", "inactive", "image"),
    ("vi", "inactive", "image"),
    ("ko", "inactive", "image"),
    ("pt", "inactive", "meme"),
    ("zh", "inactive", "video"),
    ("ar", "active",   "image"),
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


async def sobrat(tokens, url) -> tuple[list[str], list[int], bool]:
    """Прогоняем срез на STRANITS страниц, возвращаем (id, даты старта, есть ли ещё)."""
    ids, dates, cursor, has_next = [], [], None, True
    async with AsyncSession() as s:
        for _ in range(STRANITS):
            v = _build_variables(tokens.variables_template, cursor, "probe-lang", url)
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
                    await asyncio.sleep(1)
            if r is None or r.status_code != 200 or "1675004" in r.text:
                break
            d = _parse_response_json(r.text)
            src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
            for e in src.get("edges") or []:
                for cr in (e.get("node") or {}).get("collated_results") or []:
                    ids.append(str(cr.get("ad_archive_id")))
                    dates.append(cr.get("start_date"))
            pi = src.get("page_info") or {}
            cursor = pi.get("end_cursor")
            has_next = bool(pi.get("has_next_page"))
            if not cursor or not has_next:
                break
    return ids, dates, has_next


async def skolko_novyh(ids: list[str]) -> int:
    if not ids:
        return 0
    async with AsyncSessionLocal() as s:
        row = await s.execute(
            text("select count(*) from ads where library_id = any(:ids)"),
            {"ids": list(set(ids))})
        est = row.scalar() or 0
    return len(set(ids)) - est


async def run():
    tokens = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print(f"целевой день {DEN}, отсечка {OTSECHKA}, по {STRANITS} страниц на срез\n")
    print("срез                        | карточек | уник | НОВЫХ | в целевой день | ещё есть")
    itogo_novyh = 0
    for lang, status, media in PLAN:
        url = build_library_url(country="CA", keyword=None,
                                languages=[lang] if lang else None,
                                active_status=status, media_type=media,
                                sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                                date_to=OTSECHKA)
        ids, dates, has_next = await sobrat(tokens, url)
        novyh = await skolko_novyh(ids)
        itogo_novyh += novyh
        # start_date приходит unix-секундами; целевой день = 07.07.2026 07:00 UTC
        v_den = sum(1 for d in dates if d and 1783382400 <= int(d) <= 1783555200)
        label = f"{lang or '—':>3} / {status:<8} / {media:<5}"
        print(f"{label:<27} | {len(ids):>8} | {len(set(ids)):>4} | {novyh:>5} | {v_den:>14} | {has_next}")
        await asyncio.sleep(1)
    print(f"\nвсего новых за зонд: {itogo_novyh}")

asyncio.run(run())
