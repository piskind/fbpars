"""Сколько FB заявляет по слову — с датой и без неё.

Если счётчик по hearing без фильтра даты ≈ 50 тысяч, значит цифра заказчика взята
с экрана, где фильтр по дате не доехал до сервера (JS шлёт startDate={min:null,max:null},
проверено), и сравнивать её с нашим дневным сбором нельзя.
"""
import asyncio
import json
import re
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import proxy as proxy_mod
from app import token_pool

PROBY = [
    ("без даты вообще", None),
    ("отсечка 22.08 (наш дневной сбор)", "2026-08-22"),
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


async def zapros(t, otsechka):
    url = build_library_url(country="US", keyword="hearing", languages=None,
                            active_status="all", media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to=otsechka)
    v = _build_variables(t.variables_template, None, "schet", url)
    if otsechka is None:
        v["startDate"] = {"min": None, "max": None}
    fd = _build_form_data(t.as_tokens_dict(), v)
    for _ in range(10):
        px = await proxy_mod.get_proxy_url()
        try:
            async with AsyncSession() as s:
                r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                                 headers=hdr(t), impersonate="chrome131", timeout=40,
                                 proxies={"http": px, "https": px})
            if r.status_code == 200 and "1675004" not in r.text:
                return r.text
        except Exception:
            await asyncio.sleep(0.4)
    return ""


async def run():
    t = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    for imya, otsechka in PROBY:
        text = await zapros(t, otsechka)
        if not text:
            print(f"{imya}: запрос не прошёл"); continue
        d = _parse_response_json(text)
        al = (d.get("data") or {}).get("ad_library_main") or {}
        # ищем любые поля со словом count/total в ответе
        schetchiki = {k: v for k, v in al.items()
                      if isinstance(v, (int, str)) and any(s in k.lower() for s in ("count", "total"))}
        src = al.get("search_results_connection") or {}
        schetchiki.update({k: v for k, v in src.items()
                           if isinstance(v, (int, str)) and any(s in k.lower() for s in ("count", "total"))})
        # плюс грубый поиск по сырому тексту
        syrye = sorted(set(re.findall(r'"(\w*(?:count|total)\w*)":\s*(\d{3,})', text, re.I)))[:6]
        print(f"--- {imya}")
        print(f"    поля-счётчики в ответе: {schetchiki or 'нет'}")
        print(f"    похожие числа в сыром ответе: {syrye or 'нет'}")
        print(f"    ключи ad_library_main: {sorted(al.keys())}")

asyncio.run(run())
