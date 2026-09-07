"""Матрица связок: какие СОЧЕТАНИЯ осей ещё дают новое на выбранном дне.

Одиночные оси мы уже знаем (язык 13.7 крео/срез, слово×медиа 5.1, хвост словаря 1.5).
Здесь проверяем сочетания, которые не пробовались, на равном бюджете страниц — и
оставляем те, что окупаются. Мерим единственную важную величину: новые крео В ЦЕЛЕВОМ
ДНЕ (остальное воркер отбрасывает по only_day).

Главная гипотеза: наш словарь на 40 000 слов намыт из англоязычной выдачи США, поэтому
к корпусам zh/ar/th/he/ko мы вообще не подступались — там слова другие. Если связка
«язык + слово на этом языке» окупается, это открывает объём, которого сейчас нет.

Запуск:  docker exec fbpars-parser-worker-1 python -u -m app.probe_svyazki
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
STRANITS = 10                       # равный бюджет на связку: 10 страниц = 100 карточек
DEN_OT, DEN_DO = 1783382400, 1783555200

# Слова на своих языках — их в нашем словаре нет вовсе (он намыт из выдачи США).
SLOVA = {
    "zh": ["优惠", "免费", "减肥", "投资"],
    "ar": ["مجانا", "تخفيض", "ربح", "تخسيس"],
    "th": ["ลดน้ำหนัก", "ฟรี", "เงิน"],
    "he": ["חינם", "הנחה"],
    "ko": ["무료", "다이어트"],
    "ja": ["無料", "ダイエット"],
}

SVYAZKI = []
# 1. контроль: чистый язык (знаем, что работает)
for lang in ("zh", "ar", "pt"):
    SVYAZKI.append({"name": f"{lang} × image (контроль)", "languages": [lang], "media_type": "image"})
# 2. язык × платформа
for pl in ("facebook", "instagram", "audience_network", "messenger"):
    SVYAZKI.append({"name": f"zh × image × {pl}", "languages": ["zh"], "media_type": "image",
                    "platforms": [pl]})
# 3. язык × спец-архив
SVYAZKI.append({"name": "zh × image × political", "languages": ["zh"], "media_type": "image",
                "ad_type": "political_and_issue_ads"})
# 4. язык × слово НА ЭТОМ ЯЗЫКЕ (главная гипотеза)
for lang, slova in SLOVA.items():
    for w in slova[:2]:
        SVYAZKI.append({"name": f"{lang} × слово {w!r}", "languages": [lang], "keyword": w})
# 5. слово на языке БЕЗ языкового фильтра — проверяем, нужен ли фильтр вообще
SVYAZKI.append({"name": "слово '优惠' без языка", "keyword": "优惠"})
SVYAZKI.append({"name": "слово 'مجانا' без языка", "keyword": "مجانا"})


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


async def probovat(tokens, svyazka):
    url = build_library_url(
        country="CA", keyword=svyazka.get("keyword"),
        languages=svyazka.get("languages"),
        active_status="inactive", media_type=svyazka.get("media_type", "all"),
        platforms=svyazka.get("platforms"),
        ad_type=svyazka.get("ad_type", "all"),
        sort_mode="relevancy_monthly_grouped", sort_direction="desc",
        date_to=OTSECHKA)
    cursor, vsego, v_dne = None, 0, []
    async with AsyncSession() as s:
        for _ in range(STRANITS):
            v = _build_variables(tokens.variables_template, cursor, "svyaz", url)
            fd = _build_form_data(tokens.as_tokens_dict(), v)
            r = None
            for _ in range(12):
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
            pi = src.get("page_info") or {}
            cursor = pi.get("end_cursor")
            if not cursor or not pi.get("has_next_page"):
                break
    return vsego, len(v_dne), await novyh_iz(v_dne)


async def run():
    tokens = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    print(f"CA / 7 июля, inactive, по {STRANITS} страниц на связку\n")
    print(f"{'связка':<34} | карточек | в дне | НОВЫХ")
    itogi = []
    # по три связки за раз: пул и так на 36% брака
    for i in range(0, len(SVYAZKI), 3):
        pachka = SVYAZKI[i:i + 3]
        rez = await asyncio.gather(*[probovat(tokens, s) for s in pachka])
        for s, (vsego, v_dne, novyh) in zip(pachka, rez):
            itogi.append((novyh, s["name"], vsego, v_dne))
            print(f"{s['name']:<34} | {vsego:>8} | {v_dne:>5} | {novyh:>5}")
    print("\nпо убыванию отдачи:")
    for novyh, name, vsego, v_dne in sorted(itogi, reverse=True):
        if novyh:
            print(f"  {name}: {novyh} новых (из {vsego} карточек)")
    print(f"\nвсего новых за пробу: {sum(x[0] for x in itogi)}")

asyncio.run(run())
