"""Проба: работает ли ОДНА токен-сессия для РАЗНЫХ ключей/статусов/гео.

Если да — захват токенов (сейчас ~50 с на срез) можно делать раз в N минут на всех
воркеров, а не на каждый срез.
"""
import asyncio, json, time
from urllib.parse import urlencode
from curl_cffi.requests import AsyncSession
from app.browser import capture_session_tokens, build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app import proxy as proxy_mod

def mk(country, kw, active):
    return build_library_url(country=country, keyword=kw, languages=None,
                             active_status=active, media_type="all",
                             sort_mode="relevancy_monthly_grouped", sort_direction="desc")

def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h

async def ask(s, t, *, kw, country, active, start_max, cursor=None):
    v = {**t.variables_template, "cursor": cursor, "sessionID": f"probe-{kw}",
         "queryString": kw, "countries": [country], "activeStatus": active,
         "startDate": {"min": None, "max": start_max}}
    fd = _build_form_data(t.as_tokens_dict(), v)
    px = await proxy_mod.get_proxy_url()
    t0 = time.time()
    r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                     headers=hdr(t), impersonate="chrome131", timeout=40,
                     proxies={"http": px, "https": px})
    dt = time.time() - t0
    if r.status_code != 200:
        return f"HTTP {r.status_code}", dt
    if "1675004" in r.text:
        return "RATE-LIMIT", dt
    d = _parse_response_json(r.text)
    src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
    edges = src.get("edges") or []
    ids, pages_names = [], []
    for e in edges:
        for cr in (e.get("node") or {}).get("collated_results") or []:
            ids.append(cr.get("ad_archive_id"))
            pages_names.append(cr.get("page_name"))
    err = d.get("errors")
    return {"edges": len(edges), "ads": len(ids), "has_next": bool((src.get("page_info") or {}).get("has_next_page")),
            "sample_pages": pages_names[:3], "errors": str(err)[:120] if err else None,
            "first_id": ids[0] if ids else None}, dt

async def run():
    url = mk("CA", "clinic", "active")
    t = await capture_session_tokens(url)
    print(f"tokens captured for CA/clinic/active  doc_id={t.doc_id}\n")
    async with AsyncSession() as s:
        cases = [
            ("same kw  CA/clinic/active   ", dict(kw="clinic",  country="CA", active="active",   start_max="2026-07-08")),
            ("other kw CA/dentist/active  ", dict(kw="dentist", country="CA", active="active",   start_max="2026-07-08")),
            ("other kw CA/seguro/inactive ", dict(kw="seguro",  country="CA", active="inactive", start_max="2026-07-08")),
            ("other geo US/dentist/active ", dict(kw="dentist", country="US", active="active",   start_max="2026-06-02")),
            ("no kw    MX/none/all        ", dict(kw="",        country="MX", active="all",      start_max="2026-07-08")),
        ]
        for label, kwargs in cases:
            res, dt = await ask(s, t, **kwargs)
            print(f"{label} -> {res}  ({dt:.1f}s)")
            await asyncio.sleep(1)

        # пагинация чужим ключом на этой же сессии: 3 страницы подряд
        print("\nпагинация чужим ключом (CA/dentist), 3 страницы:")
        cur = None
        for i in range(3):
            v = {**t.variables_template, "cursor": cur, "sessionID": "probe-pag",
                 "queryString": "dentist", "countries": ["CA"], "activeStatus": "active",
                 "startDate": {"min": None, "max": "2026-07-08"}}
            fd = _build_form_data(t.as_tokens_dict(), v)
            px = await proxy_mod.get_proxy_url()
            r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                             headers=hdr(t), impersonate="chrome131", timeout=40,
                             proxies={"http": px, "https": px})
            d = _parse_response_json(r.text)
            src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
            edges = src.get("edges") or []
            pi = src.get("page_info") or {}
            cur = pi.get("end_cursor")
            print(f"  page={i+1} edges={len(edges)} has_next={pi.get('has_next_page')} cursor={'yes' if cur else 'no'}")
            if not cur:
                break

asyncio.run(run())
