"""Снимаем variables-шаблон для разных URL, чтобы узнать точную кодировку значений."""
import asyncio, json
from app.browser import capture_session_tokens, build_library_url

CASES = [
    ("CA/clinic/active/all-media",
     dict(country="CA", keyword="clinic", active_status="active", media_type="all",
          sort_mode="relevancy_monthly_grouped", sort_direction="desc", date_to="2026-07-08")),
    ("US/none/inactive/video+langs+platform",
     dict(country="US", keyword=None, active_status="inactive", media_type="video",
          languages=["es"], platforms=["facebook"],
          sort_mode="total_impressions", sort_direction="desc", date_to="2026-06-02")),
]

async def run():
    for label, kw in CASES:
        url = build_library_url(languages=kw.pop("languages", None),
                                platforms=kw.pop("platforms", None), **kw)
        t = await capture_session_tokens(url)
        print(f"--- {label}\nURL: {url}")
        print(json.dumps(t.variables_template, ensure_ascii=False, indent=1, sort_keys=True))
        print()

asyncio.run(run())
