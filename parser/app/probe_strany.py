"""Приходят ли в срез по США объявления других стран.

Память говорит: гео у FB не фильтрует ни в keyword-поиске, ни в browse (замер 04.08).
Проверяем предметно: запрашиваем страну US и смотрим, что в самих карточках — какие поля
про страну там вообще есть и что в них лежит.
"""
import asyncio
import json
from collections import Counter
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import proxy as proxy_mod
from app import token_pool

DEN, OTSECHKA = "2026-08-21", "2026-08-22"
SLOVA = ["Diabetes", "Weight loss", "Casino", "Prostatitis", "Hypertension",
         "Vision", "Loan", "Crypto"]


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def vzyat(t, slovo, strana):
    url = build_library_url(country=strana, keyword=slovo, languages=None,
                            active_status="all", media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to=OTSECHKA)
    v = _build_variables(t.variables_template, None, "strany", url)
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
        return []
    d = _parse_response_json(r.text)
    src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
    kartochki = []
    for e in src.get("edges") or []:
        for cr in (e.get("node") or {}).get("collated_results") or []:
            kartochki.append(cr)
    return kartochki


async def run():
    t = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))

    # 1. какие поля про страну вообще есть в карточке
    k = await vzyat(t, "Diabetes", "US")
    if k:
        polya = sorted(p for p in k[0] if any(s in p.lower()
                                              for s in ("countr", "region", "reach", "location", "target")))
        print("поля карточки, связанные со страной:", polya)
        for p in polya:
            print(f"   {p} = {json.dumps(k[0].get(p), ensure_ascii=False)[:120]}")
        print()

    # 2. одинаковая ли выдача для разных стран — если гео не фильтрует, наборы совпадут
    doli = []
    for slovo in SLOVA:
        us = await vzyat(t, slovo, "US")
        de = await vzyat(t, slovo, "DE")
        us_id = {c.get("ad_archive_id") for c in us}
        de_id = {c.get("ad_archive_id") for c in de}
        peresech = len(us_id & de_id)
        baza = min(len(us_id), len(de_id)) or 1
        doli.append(peresech / baza)
        print(f"{slovo:<14} US={len(us_id)} DE={len(de_id)} общих={peresech} ({peresech*100//baza}%)")
    print(f"\nсредняя доля общих объявлений между США и Германией: {sum(doli)/len(doli)*100:.0f}%")

    # 3. языки в срезе по США — косвенный признак чужих стран
    yaz = Counter()
    for slovo in SLOVA:
        for c in await vzyat(t, slovo, "US"):
            sn = (c.get("snapshot") or {})
            txt = (sn.get("body") or {}).get("text") or sn.get("title") or ""
            yaz["с латиницей" if all(ord(ch) < 128 for ch in txt[:60]) else "НЕ латиница"] += 1
    print("\nтексты в срезе по США:", dict(yaz))

asyncio.run(run())
