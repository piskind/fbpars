"""
Measure Ad Library scroll throughput for a country without saving anything.

Goal: estimate ads/min scroll speed and find the DOM retention ceiling
(how many cards FB keeps in memory before evicting old ones).

Usage:
    python -m app.count_geo --country=AM
    python -m app.count_geo --country=DE --max_scrolls=100

Start with small geos (AM, CY) to validate, then scale up.
DOM eviction on large geos (50k+ ads) is expected and logged explicitly.
"""
import asyncio
import argparse
from loguru import logger

from app.browser import browser_context, build_library_url_country_only
from app.parsers.library_extractor import scroll_and_count


async def run(country: str, max_scrolls: int) -> None:
    url = build_library_url_country_only(country)
    logger.info(f"[count_geo] country={country} max_scrolls={max_scrolls}")
    logger.info(f"[count_geo] url={url}")

    async with browser_context() as context:
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        logger.info(f"[count_geo] final url: {page.url}")
        raw_html = await page.content()
        if "__rd_verify" in raw_html:
            logger.warning("[count_geo] FB challenge detected despite q=%25 URL — results may be empty")

        try:
            await page.wait_for_selector('div:has-text("Library ID")', timeout=20_000)
        except Exception:
            logger.warning("[count_geo] No ads found on page — geo may be empty or blocked")
            return

        await asyncio.sleep(5)
        await scroll_and_count(page, max_scrolls=max_scrolls)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Count Ad Library ads for a country (no DB/S3 writes)")
    parser.add_argument("--country", required=True, help="ISO country code, e.g. AM, CY, DE")
    parser.add_argument("--max_scrolls", type=int, default=200, help="Safety cap on scroll iterations")
    args = parser.parse_args()
    asyncio.run(run(args.country, args.max_scrolls))
