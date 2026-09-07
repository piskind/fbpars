"""Что вернёт FB на ПРОТУХШЕЙ сессии: ошибку (упадём и перезахватим) или пустую выдачу?

Это ключевой риск общего пула: если протухший lsd отдаёт пустой, но валидный ответ,
срез тихо закроется как «0 собрано» — и мы не заметим, что пропустили день.
"""
import asyncio
import json
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import capture_session_tokens, build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app.proxy import worker_gateway

URL = build_library_url(country="CA", keyword="dentist", languages=None,
                        active_status="active", media_type="all",
                        sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                        date_to="2026-07-08")


def hdr(lsd, cookies):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if cookies:
        h["cookie"] = cookies
    return h


async def ask(s, t, *, lsd=None, cookies=None, label=""):
    v = _build_variables(t.variables_template, None, "stale-probe", URL)
    fd = _build_form_data(t.as_tokens_dict(), v)
    if lsd is not None:
        fd["lsd"] = lsd
    gw = worker_gateway()
    r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                     headers=hdr(lsd if lsd is not None else t.lsd,
                                 t.cookies if cookies is None else cookies),
                     impersonate="chrome131", timeout=40,
                     proxies={"http": gw, "https": gw})
    body = r.text
    marker = "1675004" if "1675004" in body else ""
    try:
        d = _parse_response_json(body)
    except Exception as exc:
        print(f"{label}: HTTP {r.status_code}, тело не парсится ({str(exc)[:40]}) — упадём с ошибкой ✔")
        return
    src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection")
    errs = d.get("errors")
    if src is None:
        print(f"{label}: HTTP {r.status_code} rl={marker or '-'} errors={str(errs)[:80]} "
              f"→ структуры выдачи НЕТ (парсер вернёт 0 узлов и has_next=False)")
        return
    edges = src.get("edges") or []
    ads = sum(len((e.get("node") or {}).get("collated_results") or []) for e in edges)
    pi = src.get("page_info") or {}
    print(f"{label}: HTTP {r.status_code} rl={marker or '-'} объяв={ads} "
          f"has_next={pi.get('has_next_page')} errors={str(errs)[:60]}")


async def run():
    t = await capture_session_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    async with AsyncSession() as s:
        await ask(s, t, label="целая сессия          ")
        await ask(s, t, lsd="AAAAAAAAAAAAAAAAAAAAAA", label="битый lsd             ")
        await ask(s, t, lsd="", label="пустой lsd            ")
        await ask(s, t, cookies="", label="без cookies           ")

asyncio.run(run())
