"""Что интерфейс библиотеки шлёт в запрос, когда в нём выставлен фильтр по дате.

Заказчик видит 50 тысяч по hearing за 21 августа. Наш запрос с накопительной отсечкой
столько не отдаёт. Значит интерфейс фильтрует иначе — снимаем переменные прямо с его
запроса: открываем страницу с диапазоном дат и смотрим, что уходит в GraphQL.
"""
import asyncio
import json

from app.browser import capture_session_tokens

URLY = [
    ("только [max] (как шлём мы)",
     "https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US"
     "&q=hearing&search_type=keyword_unordered&media_type=all"
     "&sort_data%5Bmode%5D=relevancy_monthly_grouped&sort_data%5Bdirection%5D=desc"
     "&start_date%5Bmax%5D=2026-08-22"),
    ("диапазон [min..max] 21-21 августа",
     "https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US"
     "&q=hearing&search_type=keyword_unordered&media_type=all"
     "&sort_data%5Bmode%5D=relevancy_monthly_grouped&sort_data%5Bdirection%5D=desc"
     "&start_date%5Bmin%5D=2026-08-21&start_date%5Bmax%5D=2026-08-21"),
]

INTERESNO = ("startDate", "activeStatus", "queryString", "countries", "searchType",
             "sortData", "v", "source", "collationToken", "potentialReachInput")


async def run():
    for imya, url in URLY:
        try:
            t = await capture_session_tokens(url)
        except Exception as exc:
            print(f"{imya}: захват не удался — {str(exc)[:80]}\n")
            continue
        v = t.variables_template
        print(f"--- {imya}")
        for p in INTERESNO:
            if p in v:
                print(f"    {p} = {json.dumps(v[p], ensure_ascii=False)}")
        prochee = [p for p in v if p not in INTERESNO and v[p] not in (None, [], "", {})]
        if prochee:
            print(f"    прочие непустые: {', '.join(prochee)}")
        print()

asyncio.run(run())
