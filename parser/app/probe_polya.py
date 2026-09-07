"""Есть ли в карточке дата ОКОНЧАНИЯ показа — чтобы считать «крутившиеся в день».

Заказчик видит по слову hearing 50 тысяч за 21 августа, у нас 278. Разница в метрике:
мы сохраняем только СТАРТОВАВШИЕ в этот день, а он считает КРУТИВШИЕСЯ — то есть
стартовавшие раньше и ещё не остановленные.

Чтобы собирать так же, нужна дата окончания: «крутилось 21-го» = стартовало <= 21.08
И (остановлено >= 21.08 ИЛИ ещё идёт). Смотрим, отдаёт ли FB такое поле.
"""
import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession

from app.browser import build_library_url
from app.graphql_client import _build_form_data, _parse_response_json
from app.graphql_paginator import _build_variables
from app import proxy as proxy_mod
from app import token_pool


def hdr(t):
    h = {"content-type": "application/x-www-form-urlencoded",
         "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
         "x-fb-lsd": t.lsd or "", "x-asbd-id": "359341",
         "origin": "https://www.facebook.com",
         "referer": "https://www.facebook.com/ads/library/"}
    if t.cookies:
        h["cookie"] = t.cookies
    return h


async def run():
    t = await token_pool.get_tokens(build_library_url(
        country="US", keyword=None, languages=None, active_status="all", media_type="all",
        sort_mode="relevancy_monthly_grouped", sort_direction="desc"))
    url = build_library_url(country="US", keyword="Hearing", languages=None,
                            active_status="all", media_type="all",
                            sort_mode="relevancy_monthly_grouped", sort_direction="desc",
                            date_to="2026-08-22")
    v = _build_variables(t.variables_template, None, "polya", url)
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
    d = _parse_response_json(r.text)
    src = ((d.get("data") or {}).get("ad_library_main") or {}).get("search_results_connection") or {}
    kartochki = [cr for e in (src.get("edges") or [])
                 for cr in ((e.get("node") or {}).get("collated_results") or [])]
    if not kartochki:
        print("карточек не пришло"); return

    k = kartochki[0]
    print("ВСЕ поля карточки:")
    print("  " + ", ".join(sorted(k.keys())))
    print()
    print("поля с датами:")
    for p in sorted(k):
        if any(s in p.lower() for s in ("date", "time", "delivery", "active", "stop", "end")):
            znach = k.get(p)
            if isinstance(znach, (int, float)) and znach > 1_000_000_000:
                znach = f"{znach} = {datetime.fromtimestamp(int(znach), timezone.utc).date()}"
            print(f"  {p} = {json.dumps(znach, ensure_ascii=False)[:100] if not isinstance(znach, str) else znach}")
    print()
    # сколько среди пришедших уже остановлены и есть ли у них конец показа
    ostanovleny = [c for c in kartochki if not c.get("is_active")]
    print(f"карточек {len(kartochki)}, из них остановленных {len(ostanovleny)}")
    for c in ostanovleny[:3]:
        st = c.get("start_date")
        en = c.get("end_date")
        print(f"  старт {datetime.fromtimestamp(int(st), timezone.utc).date() if st else '—'}"
              f" | конец {datetime.fromtimestamp(int(en), timezone.utc).date() if en else 'НЕТ ПОЛЯ'}")

asyncio.run(run())
