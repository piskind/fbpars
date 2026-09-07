"""Почему по «nolimit city» библиотека показывает ноль, а мы нашли 8 397.

Гипотеза: у FB два режима поиска. Наш URL шлёт search_type=keyword_unordered — «слова в
любом порядке», это режим библиотеки по умолчанию, и он матчит объявления, где встречается
ЛЮБОЕ из слов. Если заказчик искал точной фразой (keyword_exact_phrase), результат будет
совсем другим.

Сравниваем оба режима на одном и том же слове и смотрим, что за объявления приходят.
"""
import asyncio
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import proxy as proxy_mod
from app import token_pool

OTSECHKA = "2026-08-22"
SLOVA = ["NoLimit City", "nolimit city", "Nolimit"]


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def poisk(t, slovo, rezhim):
    url = build_library_url(country="US", keyword=slovo, languages=None,
                            active_status="active", media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to=OTSECHKA)
    url = url.replace("search_type=keyword_unordered", f"search_type={rezhim}")
    v = _build_variables(t.variables_template, None, "poisk", url)
    v["searchType"] = rezhim
    fd = _build_form_data(t.as_tokens_dict(), v)
    for _ in range(10):
        px = await proxy_mod.get_proxy_url()
        try:
            async with AsyncSession() as s:
                r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                                 headers=hdr(t), impersonate="chrome131", timeout=40,
                                 proxies={"http": px, "https": px})
            if r.status_code == 200 and "1675004" not in r.text:
                break
        except Exception:
            await asyncio.sleep(0.4)
    else:
        return None, []
    d = _parse_response_json(r.text)
    src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
    kartochki = [cr for e in (src.get("edges") or [])
                 for cr in ((e.get("node") or {}).get("collated_results") or [])]
    primery = []
    for c in kartochki[:4]:
        sn = c.get("snapshot") or {}
        txt = ((sn.get("body") or {}).get("text") or sn.get("title") or "")[:60]
        primery.append(f"{c.get('page_name','?')[:22]} | {txt}")
    return len(kartochki), primery


async def run():
    t = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    for slovo in SLOVA:
        for rezhim in ("keyword_unordered", "keyword_exact_phrase"):
            n, primery = await poisk(t, slovo, rezhim)
            print(f"{slovo!r:<18} {rezhim:<22} карточек на странице: {n}")
            for p in primery:
                print(f"      {p}")
        print()

asyncio.run(run())
