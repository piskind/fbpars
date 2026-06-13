import asyncio
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, BrowserContext
from app.config import settings
from loguru import logger

# At most one Chromium process alive at a time.
# Prevents refresh_worker + discovery_poll + manual tools from stacking 2-3 × 2.7 GB.
_BROWSER_SEMAPHORE = asyncio.Semaphore(1)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


@asynccontextmanager
async def browser_context():
    async with _BROWSER_SEMAPHORE:
        async with async_playwright() as pw:
            browser: Browser = await pw.chromium.launch(
                headless=settings.headless,
                proxy={"server": settings.proxy_http_gateway},
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--disable-backgrounding-occluded-windows",
                    "--js-flags=--max-old-space-size=768",
                    "--disk-cache-size=1",
                    "--media-cache-size=1",
                    "--renderer-process-limit=1",
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


async def goto_with_challenge_retry(
    page,
    url: str,
    max_attempts: int = 4,
    base_wait: int = 6,
) -> bool:
    """
    Navigate to url, handling FB's __rd_verify anti-bot challenge.

    Returns True if page loaded cleanly, False if challenge persisted after all attempts.

    FB's __rd_verify injects JS that calls location.reload() automatically.
    Strategy: wait for that auto-reload, re-check; if still stuck, force a fresh goto.
    """
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as e:
        logger.warning(f"goto_with_challenge_retry: network error on initial goto: {e}")
        return False

    try:
        html = await page.content()
    except Exception:
        return False

    if "__rd_verify" not in html:
        return True

    for attempt in range(1, max_attempts + 1):
        wait_s = base_wait * attempt
        logger.warning(f"__rd_verify challenge (attempt {attempt}/{max_attempts}) — waiting {wait_s}s for auto-reload")
        await asyncio.sleep(wait_s)
        try:
            await page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        try:
            html = await page.content()
        except Exception:
            return False
        if "__rd_verify" not in html:
            logger.info(f"Challenge cleared after {attempt} wait(s)")
            return True
        if attempt < max_attempts:
            logger.warning("Still challenged — forcing fresh goto")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                html = await page.content()
                if "__rd_verify" not in html:
                    logger.info(f"Challenge cleared after fresh goto (attempt {attempt})")
                    return True
            except Exception:
                pass

    logger.warning(f"__rd_verify persisted after {max_attempts} attempts")
    return False


def build_library_url(country: str, keyword: str, languages: list[str] | None = None) -> str:
    from urllib.parse import quote
    url = (
        "https://www.facebook.com/ads/library/"
        f"?active_status=all&ad_type=all&country={country}"
        f"&q={quote(keyword)}&search_type=keyword_unordered&media_type=all"
    )
    for i, lang in enumerate(languages or []):
        url += f"&content_languages%5B{i}%5D={lang}"
    return url


def build_library_url_country_only(
    country: str,
    is_targeted_country: bool | None = None,
    q: str = "%25",
) -> str:
    url = (
        "https://www.facebook.com/ads/library/"
        f"?active_status=all&ad_type=all&country={country}"
        f"&q={q}&search_type=keyword_unordered&media_type=all"
    )
    if is_targeted_country is not None:
        url += f"&is_targeted_country={'true' if is_targeted_country else 'false'}"
    return url