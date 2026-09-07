"""Гейт корректности: общая сессия + подмена переменных из URL == нынешний путь.

Слева — как сейчас: токены захвачены НА URL среза, переменные из шаблона.
Справа — как станет: токены захвачены на постороннем широком URL, переменные из URL среза.
Сравниваем множества ad_archive_id по 5 страницам подряд.
"""
import asyncio
import json
from urllib.parse import urlencode, urlparse, parse_qs

from curl_cffi.requests import AsyncSession

from app.browser import capture_session_tokens, build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app import proxy as proxy_mod

_SORT_DIR = {"desc": "DESCENDING", "asc": "ASCENDING"}


def _start_date_from_url(url):
    q = parse_qs(urlparse(url).query)
    hi = (q.get("start_date[max]") or [None])[0]
    if not hi:
        return None
    return {"min": None, "max": hi}


def _query_vars_from_url(url: str) -> dict:
    q = parse_qs(urlparse(url).query)

    def one(name, default=None):
        v = q.get(name)
        return v[0] if v else default

    def indexed(prefix):
        out = []
        for i in range(20):
            v = q.get(f"{prefix}[{i}]")
            if not v:
                break
            out.append(v[0])
        return out

    country = one("country", "")
    mode = one("sort_data[mode]", "total_impressions")
    direction = one("sort_data[direction]", "desc")
    v = {
        "activeStatus": one("active_status", "all"),
        "adType": (one("ad_type", "all") or "all").upper(),
        "countries": [country] if country else [],
        "queryString": one("q", ""),
        "searchType": one("search_type", "keyword_unordered"),
        "mediaType": one("media_type", "all"),
        "contentLanguages": indexed("content_languages"),
        "publisherPlatforms": indexed("publisher_platforms"),
        "sortData": {"mode": f"SORT_BY_{mode.upper()}", "direction": _SORT_DIR.get(direction, "DESCENDING")},
        "bylines": [], "pageIDs": [], "excludedIDs": None, "collationToken": None,
        "viewAllPageID": "0",
    }
    itc = one("is_targeted_country")
    if itc is not None:
        v["isTargetedCountry"] = (itc == "true")
    sd = _start_date_from_url(url)
    v["startDate"] = sd if sd is not None else {"min": None, "max": None}
    return v


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def crawl(s, tokens, url, pages, override):
    """Пагинируем `pages` страниц; override=True — подменяем переменные из URL."""
    cursor, ids, dates = None, [], []
    for i in range(pages):
        v = {**tokens.variables_template, "cursor": cursor, "sessionID": "equiv-probe"}
        if override:
            v.update(_query_vars_from_url(url))
        else:
            sd = _start_date_from_url(url)
            if sd is not None:
                v["startDate"] = sd
        fd = _build_form_data(tokens.as_tokens_dict(), v)
        r = None
        for _ in range(6):  # битый residential IP — обычное дело, берём следующий
            px = await proxy_mod.get_proxy_url()
            try:
                r = await s.post("https://www.facebook.com/api/graphql/", data=urlencode(fd),
                                 headers=hdr(tokens), impersonate="chrome131", timeout=40,
                                 proxies={"http": px, "https": px})
                break
            except Exception as exc:
                print(f"    стр.{i+1}: транспорт ({str(exc)[:60]}) — другой IP")
                await asyncio.sleep(1)
        if r is None:
            print(f"    стр.{i+1}: не достучались")
            break
        if r.status_code != 200 or "1675004" in r.text:
            print(f"    стр.{i+1}: сбой status={r.status_code} rl={'1675004' in r.text}")
            break
        d = _parse_response_json(r.text)
        src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
        for e in src.get("edges") or []:
            for cr in (e.get("node") or {}).get("collated_results") or []:
                ids.append(cr.get("ad_archive_id"))
                dates.append(cr.get("start_date"))
        pi = src.get("page_info") or {}
        cursor = pi.get("end_cursor")
        if not cursor:
            break
    return ids, dates


async def run():
    SLICE = build_library_url(country="CA", keyword="dentist", languages=None,
                              active_status="active", media_type="all",
                              sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                              date_to="2026-07-08")
    BROAD = build_library_url(country="US", keyword=None, languages=None,
                              active_status="all", media_type="all",
                              sort_mode="relevancy_monthly_grouped", sort_direction="desc")

    async def cap(u, label):
        for attempt in range(4):  # захват браузером сам по себе флапает — это и лечим пулом
            try:
                t = await capture_session_tokens(u)
                if t.base_form_data:
                    return t
            except Exception as exc:
                print(f"    захват {label} попытка {attempt+1}: {str(exc)[:80]}")
        raise RuntimeError(f"не смог захватить {label}")

    own = await cap(SLICE, "своей сессии")
    foreign = await cap(BROAD, "чужой сессии")
    print(f"своя сессия: doc_id={own.doc_id}, q={own.variables_template.get('queryString')!r}, "
          f"country={own.variables_template.get('countries')}")
    print(f"чужая сессия: doc_id={foreign.doc_id}, q={foreign.variables_template.get('queryString')!r}, "
          f"country={foreign.variables_template.get('countries')}")

    async with AsyncSession() as s:
        print("\n[A] нынешний путь (своя сессия, шаблон как есть):")
        a_ids, a_dates = await crawl(s, own, SLICE, 5, override=False)
        print(f"    собрано {len(a_ids)} id, уникальных {len(set(a_ids))}")

        print("[B] новый путь (чужая сессия + переменные из URL):")
        b_ids, b_dates = await crawl(s, foreign, SLICE, 5, override=True)
        print(f"    собрано {len(b_ids)} id, уникальных {len(set(b_ids))}")

    A, B = set(a_ids), set(b_ids)
    inter = A & B
    print(f"\nпересечение: {len(inter)} из {len(A)}/{len(B)}  "
          f"(A\\B={len(A - B)}, B\\A={len(B - A)})")
    ok_dates = all(d and d for d in b_dates[:10])
    print(f"даты старта в новом пути присутствуют: {ok_dates}; пример: {b_dates[:3]}")
    verdict = "СОВПАДАЕТ" if len(inter) >= 0.8 * min(len(A), len(B)) and B else "РАСХОЖДЕНИЕ"
    print(f"ВЕРДИКТ: {verdict}")

asyncio.run(run())
