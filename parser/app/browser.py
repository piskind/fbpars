from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from app.config import settings
from loguru import logger


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


@asynccontextmanager
async def browser_context():
    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=settings.headless,
            proxy={"server": settings.proxy_http_gateway},
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context: BrowserContext = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        try:
            yield context
        finally:
            await context.close()
            await browser.close()


def build_library_url(country: str, keyword: str) -> str:
    from urllib.parse import quote
    return (
        "https://www.facebook.com/ads/library/"
        f"?active_status=all&ad_type=all&country={country}"
        f"&q={quote(keyword)}&search_type=keyword_unordered&media_type=all"
    )


def build_library_url_country_only(country: str) -> str:
    return (
        "https://www.facebook.com/ads/library/"
        f"?active_status=all&ad_type=all&country={country}"
        f"&q=%25&search_type=keyword_unordered&media_type=all"
    )