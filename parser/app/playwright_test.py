import asyncio
from loguru import logger
from app.browser import browser_context, build_library_url


async def main():
    keyword = "oxys"
    country = "PE"
    url = build_library_url(country, keyword)
    logger.info(f"Opening: {url}")

    async with browser_context() as context:
        page = await context.new_page()

        logger.info("Navigating to library")
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        logger.info("Waiting for cards or empty state")
        try:
            await page.wait_for_selector(
                'div:has-text("Library ID"), div:has-text("results")',
                timeout=30_000,
            )
            logger.info("Content appeared")
        except Exception as e:
            logger.warning(f"No content selector found: {e}")

        await asyncio.sleep(5)

        title = await page.title()
        logger.info(f"Page title: {title}")

        screenshot_path = "/tmp/library_test.png"
        await page.screenshot(path=screenshot_path, full_page=False)
        logger.info(f"Screenshot saved: {screenshot_path}")

        for selector in [
            '[role="article"]',
            'div[data-pagelet*="Library"]',
            'div:has-text("Library ID")',
            'div:has-text("Sponsored")',
        ]:
            count = await page.locator(selector).count()
            logger.info(f"Selector '{selector}': {count} matches")

        body_text = await page.locator("body").inner_text()
        logger.info(f"Body length: {len(body_text)} chars")
        logger.info(f"First 800 chars: {body_text[:800]}")


if __name__ == "__main__":
    asyncio.run(main())