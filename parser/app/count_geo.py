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

        # --- diagnostics: run before wait_for_selector to capture whatever FB loaded ---
        logger.info(f"[diag] final url after goto: {page.url}")

        lang = await page.evaluate("document.documentElement.lang")
        logger.info(f"[diag] page lang: {lang!r}")

        body_preview = await page.evaluate("document.body.innerText.slice(0, 500)")
        logger.info(f"[diag] body text preview:\n{body_preview}")

        debug_prefix = f"/app/debug_{country}"
        await page.screenshot(path=f"{debug_prefix}.png", full_page=False)
        logger.info(f"[diag] screenshot saved: {debug_prefix}.png")

        html = await page.content()
        with open(f"{debug_prefix}.html", "w", encoding="utf-8") as fh:
            fh.write(html)
        logger.info(f"[diag] HTML saved: {debug_prefix}.html ({len(html)} bytes)")
        # --- end diagnostics ---

        try:
            await page.wait_for_selector('div:has-text("Library ID")', timeout=20_000)
        except Exception:
            logger.warning("[count_geo] No ads found on page — geo may be empty or blocked")
            logger.warning("[count_geo] Check screenshot and HTML in /app/ for clues")
            return

        await asyncio.sleep(5)
        await scroll_and_count(page, max_scrolls=max_scrolls)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Count Ad Library ads for a country (no DB/S3 writes)")
    parser.add_argument("--country", required=True, help="ISO country code, e.g. AM, CY, DE")
    parser.add_argument("--max_scrolls", type=int, default=200, help="Safety cap on scroll iterations")
    args = parser.parse_args()
    asyncio.run(run(args.country, args.max_scrolls))
