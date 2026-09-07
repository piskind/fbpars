"""Проба: уважает ли FB variables['first'] в AdLibrarySearchPaginationQuery."""
import asyncio, json, os, time
from urllib.parse import urlencode
from curl_cffi.requests import AsyncSession
from app.browser import capture_session_tokens, build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.config import settings
from app import proxy as proxy_mod

URL = build_library_url(
    country="CA", keyword="clinic", languages=None,
    active_status="active", media_type="all",
    sort_mode="relevancy_monthly_grouped", sort_direction="desc",
)

def headers(lsd, cookies):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if cookies:
        h["cookie"] = cookies
    return h

async def one(session, tokens, first, cursor, proxy_url):
    v = {**tokens.variables_template, "cursor": cursor, "sessionID": "probe-1"}
    if first:
        v["first"] = first
    fd = _build_form_data(tokens.as_tokens_dict(), v)
    t0 = time.time()
    r = await session.post("https://www.facebook.com/api/graphql/",
                           data=urlencode(fd), headers=headers(tokens.lsd, tokens.cookies),
                           impersonate="chrome131", timeout=40,
                           proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None)
    dt = time.time() - t0
    if r.status_code != 200 or "1675004" in r.text:
        return None, None, dt, f"status={r.status_code} rl={'1675004' in r.text}"
    d = _parse_response_json(r.text)
    src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
    edges = src.get("edges") or []
    ads = sum(len((e.get("node") or {}).get("collated_results") or []) for e in edges)
    pi = src.get("page_info") or {}
    return len(edges), ads, dt, pi.get("end_cursor")

async def main():
    print("template first =", json.dumps(
        {k: v for k, v in (await tok()).variables_template.items() if k in ("first","v","count","adType")}))

async def tok():
    return await capture_session_tokens(URL)

async def run():
    t = await capture_session_tokens(URL)
    print("template keys:", sorted(t.variables_template.keys()))
    print("template['first'] =", t.variables_template.get("first"))
    async with AsyncSession() as s:
        for first in (None, 10, 30, 50, 100):
            px = await proxy_mod.get_proxy_url()
            edges, ads, dt, cur = await one(s, t, first, None, px)
            print(f"first={str(first):>5}  edges={edges}  ads={ads}  {dt:.1f}s")
            await asyncio.sleep(1)

asyncio.run(run())
