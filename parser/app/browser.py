import asyncio
import json
import random
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode
from playwright.async_api import async_playwright, Browser, BrowserContext
from app.config import settings
from loguru import logger

# At most one Chromium process alive at a time.
# Prevents refresh_worker + discovery_poll + manual tools from stacking 2-3 × 2.7 GB.
_BROWSER_SEMAPHORE = asyncio.Semaphore(1)

# Token capture is short-lived (open → grab tokens → close), so we allow a small pool
# of concurrent Chromium instances just for that, independent of the heavy path above.
_TOKEN_SEMAPHORE = asyncio.Semaphore(max(1, settings.token_browser_concurrency))

# Resource types blocked to keep the browser context light (~100 MB instead of ~2.5 GB).
# We only need the DOM + GraphQL traffic; images/video/CSS/fonts are pure overhead here.
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}


async def _block_heavy_resources(context: BrowserContext) -> None:
    async def _route(route):
        try:
            if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            # Route may already be handled/closed during teardown — ignore.
            pass

    await context.route("**/*", _route)


@dataclass
class SessionTokens:
    """Everything needed to POST AdLibrarySearchPaginationQuery without a browser."""
    cookies: str = ""
    lsd: str | None = None
    doc_id: str | None = None
    base_form_data: dict = field(default_factory=dict)
    variables_template: dict = field(default_factory=dict)
    captured_at: float = 0.0

    def as_tokens_dict(self) -> dict:
        """Compat shape for graphql_client._build_form_data (expects base_form_data/lsd/doc_id)."""
        d: dict = {"base_form_data": self.base_form_data, "lsd": self.lsd}
        if self.doc_id:
            d["doc_id"] = self.doc_id
        return d


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


@asynccontextmanager
async def browser_context(
    block_resources: bool = False,
    semaphore: asyncio.Semaphore | None = None,
):
    """Launch a locked-down Chromium context.

    block_resources=True aborts image/media/font/CSS loads (light ~100 MB context) —
    use it for token capture and the browser-fetch fallback where we never render media.
    semaphore lets callers use the short-lived _TOKEN_SEMAPHORE pool instead of the
    global single-Chromium _BROWSER_SEMAPHORE.
    """
    from app.proxy import worker_gateway  # local import avoids a module-load cycle
    sem = semaphore or _BROWSER_SEMAPHORE
    async with sem:
        async with async_playwright() as pw:
            browser: Browser = await pw.chromium.launch(
                headless=settings.headless,
                # Same exit channel as this worker's curl path (worker_gateway) so token
                # capture and pagination share one IP; single-channel setups are unchanged.
                proxy={"server": worker_gateway()},
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
            if block_resources:
                await _block_heavy_resources(context)
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


def build_library_url(
    country: str,
    keyword: str | None,
    languages: list[str] | None = None,
    active_status: str = "all",
    media_type: str = "all",
    platforms: list[str] | None = None,
    date_from=None,
    date_to=None,
    advertiser: str | None = None,
    ad_type: str = "all",
    sort_mode: str = "total_impressions",
    sort_direction: str = "desc",
    is_targeted_country: bool | None = None,
) -> str:
    from urllib.parse import quote
    # Braille blank U+2800 — invisible keyword that returns broad results
    q = quote(keyword) if keyword else "%E2%A0%80"
    url = (
        "https://www.facebook.com/ads/library/"
        f"?active_status={active_status}&ad_type={ad_type}&country={country}"
        f"&q={q}&search_type=keyword_unordered&media_type={media_type}"
        f"&sort_data%5Bmode%5D={sort_mode}&sort_data%5Bdirection%5D={sort_direction}"
    )
    if is_targeted_country is not None:
        url += f"&is_targeted_country={'true' if is_targeted_country else 'false'}"
    for i, lang in enumerate(languages or []):
        url += f"&content_languages%5B{i}%5D={lang}"
    for i, platform in enumerate(platforms or []):
        url += f"&publisher_platforms%5B{i}%5D={platform}"
    if date_from is not None:
        url += f"&start_date%5Bmin%5D={date_from}"
    if date_to is not None:
        url += f"&start_date%5Bmax%5D={date_to}"
    if advertiser:
        # TODO: determine correct FB Ad Library URL parameter for advertiser filter.
        # Possibly search_type=page with a different q, or a dedicated param.
        # Log the intended value and skip for now.
        logger.warning(
            f"[build_library_url] advertiser filter not yet mapped to a URL param "
            f"— skipped. advertiser={advertiser!r}"
        )
    return url


_SESSION_FIELDS = {"lsd", "jazoest", "__s", "__dyn", "__csr", "__rev", "__hsi"}


async def _setup_token_capture(page, captured: dict) -> tuple[asyncio.Event, asyncio.Event]:
    """
    Register a request listener on page that captures GraphQL session tokens
    and base_form_data. Returns (any_gql_event, pagination_event).
    """
    any_gql_event = asyncio.Event()
    pagination_event = asyncio.Event()

    def _handle_request(request):
        if "/api/graphql" not in request.url or request.method != "POST":
            return
        try:
            post_data = request.post_data or ""
            parsed = parse_qs(post_data)

            if not any_gql_event.is_set():
                for field in _SESSION_FIELDS:
                    if field in parsed and field not in captured:
                        captured[field] = parsed[field][0]
                if captured.get("lsd"):
                    any_gql_event.set()

            if "AdLibrarySearchPaginationQuery" in post_data and not pagination_event.is_set():
                captured["base_form_data"] = {k: v[0] for k, v in parsed.items() if v}
                if "doc_id" in parsed:
                    captured["doc_id"] = parsed["doc_id"][0]
                pagination_event.set()

        except Exception as exc:
            logger.warning(f"[tokens] request parse error: {exc}")

    page.on("request", _handle_request)
    return any_gql_event, pagination_event


async def _load_and_scroll(page, url: str, pagination_event: asyncio.Event) -> None:
    """Navigate to url (handling __rd_verify) then scroll to trigger pagination query."""
    loaded = await goto_with_challenge_retry(page, url)
    if not loaded:
        raise RuntimeError(f"[tokens] page load failed: {url}")

    await asyncio.sleep(3)
    for _ in range(6):
        if pagination_event.is_set():
            break
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)


async def extract_session_tokens(url: str) -> dict:
    """
    Standalone helper: opens browser, extracts tokens, closes browser.
    For pagination use scrape_via_browser_graphql() instead.
    """
    captured: dict = {}
    async with browser_context() as context:
        page = await context.new_page()
        any_gql_event, pagination_event = await _setup_token_capture(page, captured)
        await _load_and_scroll(page, url, pagination_event)
        try:
            await asyncio.wait_for(any_gql_event.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            raise RuntimeError(f"[tokens] timeout: no GraphQL request fired at {url}")
        cookies = await context.cookies()
        captured["cookies"] = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    logger.info(f"[tokens] extracted: {list(captured.keys())}")
    return captured


async def capture_session_tokens(url: str) -> SessionTokens:
    """Phase 1 token capture: open a light browser, grab the pagination tokens, close.

    Blocks images/media/CSS/fonts, fires the first AdLibrarySearchPaginationQuery,
    captures cookies + form template, then closes the browser immediately. The returned
    SessionTokens drives browser-less pagination in graphql_paginator.
    """
    captured: dict = {}
    async with browser_context(block_resources=True, semaphore=_TOKEN_SEMAPHORE) as context:
        page = await context.new_page()
        _, pagination_event = await _setup_token_capture(page, captured)
        await _load_and_scroll(page, url, pagination_event)

        try:
            await asyncio.wait_for(pagination_event.wait(), timeout=25.0)
        except asyncio.TimeoutError:
            raise RuntimeError(f"[tokens] no AdLibrarySearchPaginationQuery fired at {url}")

        if not captured.get("base_form_data"):
            raise RuntimeError("[tokens] pagination fired but base_form_data not captured")

        cookies = await context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    base = captured["base_form_data"]
    try:
        variables_template = json.loads(base.get("variables", "{}"))
    except Exception:
        variables_template = {}

    tokens = SessionTokens(
        cookies=cookie_str,
        lsd=captured.get("lsd"),
        doc_id=captured.get("doc_id"),
        base_form_data=base,
        variables_template=variables_template,
        captured_at=time.time(),
    )
    logger.info(
        f"[tokens] captured session (doc_id={tokens.doc_id}, "
        f"form_fields={len(base)}, has_lsd={bool(tokens.lsd)})"
    )
    return tokens


@asynccontextmanager
async def browser_fetch_session(url: str):
    """Fallback transport: a resource-blocked browser page kept open for in-page fetch().

    Captures the pagination tokens FROM THIS SAME PAGE (so lsd/doc_id/form-template match the
    page's own cookies — reusing tokens captured in a different browser makes FB return 0 ads),
    then yields (fetch, tokens): an async fetch(form_data, lsd) -> (status, text) callable plus
    the matching SessionTokens. Used when curl_cffi keeps failing.
    """
    async with browser_context(block_resources=True, semaphore=_TOKEN_SEMAPHORE) as context:
        page = await context.new_page()
        captured = await _capture_pagination_context(page, url)

        base = captured["base_form_data"]
        try:
            variables_template = json.loads(base.get("variables", "{}"))
        except Exception:
            variables_template = {}
        tokens = SessionTokens(
            cookies=captured.get("cookies", ""),
            lsd=captured.get("lsd"),
            doc_id=captured.get("doc_id"),
            base_form_data=base,
            variables_template=variables_template,
            captured_at=time.time(),
        )

        async def _fetch(form_data: dict, lsd: str | None = None) -> tuple[int, str]:
            body = urlencode(form_data)
            headers = {
                "content-type": "application/x-www-form-urlencoded",
                "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
                "x-fb-lsd": (lsd if lsd is not None else tokens.lsd) or "",
                "x-asbd-id": "359341",
            }
            result = await page.evaluate(
                _FETCH_SCRIPT,
                {"url": "https://www.facebook.com/api/graphql/", "body": body, "headers": headers},
            )
            return result["status"], result["text"]

        yield _fetch, tokens


class _RateLimited(Exception):
    pass


async def _scrape_attempt(url: str, max_ads: int, max_scrolls: int) -> list[dict]:
    """Single attempt: open browser, intercept responses, scroll. Raises _RateLimited if blocked."""
    from app.graphql_client import _parse_response_json, _extract_ads_and_cursor

    ad_nodes: list[dict] = []
    seen_ids: set[str] = set()
    rate_limit_hits = 0

    async with browser_context() as context:
        page = await context.new_page()

        async def _on_request_finished(request):
            nonlocal rate_limit_hits
            if "/api/graphql" not in request.url:
                return
            try:
                resp = await request.response()
                if resp is None:
                    return
                text = await resp.text()
                if "1675004" in text:
                    rate_limit_hits += 1
                    logger.warning(f"[scrape] rate limit hit #{rate_limit_hits} from browser request")
                    return
                if "ad_library_main" not in text:
                    return
                parsed = _parse_response_json(text)
                nodes, _, _ = _extract_ads_and_cursor(parsed)
                new_count = 0
                for node in nodes:
                    nid = str(node.get("id") or "")
                    if nid and nid not in seen_ids:
                        seen_ids.add(nid)
                        ad_nodes.append(node)
                        new_count += 1
                if new_count:
                    logger.info(f"[scrape] +{new_count} ads (total={len(ad_nodes)})")
            except Exception as exc:
                logger.warning(f"[scrape] requestfinished error: {exc}")

        page.on("requestfinished", _on_request_finished)

        loaded = await goto_with_challenge_retry(page, url)
        if not loaded:
            raise RuntimeError(f"[scrape] page load failed: {url}")

        try:
            await page.wait_for_selector('div:has-text("Library ID")', timeout=20_000)
        except Exception:
            logger.warning("[scrape] no ads visible on page")
            return []

        await asyncio.sleep(3)

        stable = 0
        prev_count = 0
        for i in range(max_scrolls):
            if len(ad_nodes) >= max_ads:
                logger.info(f"[scrape] max_ads={max_ads} reached at scroll {i + 1}")
                break
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            current = len(ad_nodes)
            if i % 10 == 0 or current != prev_count:
                logger.info(f"[scrape] scroll={i + 1} unique_ads={current}")

            if current == prev_count:
                stable += 1
                if stable >= 5:
                    if rate_limit_hits > 0 and len(ad_nodes) == 0:
                        raise _RateLimited(f"IP rate limited after {rate_limit_hits} hits")
                    logger.info("[scrape] stable for 5 scrolls — end of feed")
                    break
            else:
                stable = 0
            prev_count = current

    logger.info(f"[scrape] done: {len(ad_nodes)} unique ads")
    return ad_nodes


async def scrape_via_browser_graphql(
    url: str,
    max_ads: int | None = None,
    max_scrolls: int = 80,
) -> list[dict]:
    """Intercept FB's GraphQL responses while scrolling. Fallback to scrape_via_page_fetch (still scrolls → RAM grows)."""
    from app.proxy import rotate_ip_verified

    if max_ads is None:
        max_ads = settings.max_ads_per_chunk
    for attempt in range(3):
        try:
            return await _scrape_attempt(url, max_ads, max_scrolls)
        except _RateLimited as exc:
            if attempt >= 2:
                raise RuntimeError(f"IP rate limited after {attempt + 1} attempts with rotation") from exc
            logger.warning(f"[scrape] {exc} — rotating IP (attempt {attempt + 1}/3)")
            if not await rotate_ip_verified():
                raise RuntimeError("IP rate limited and rotation did not change the IP") from exc

    return []


# Runs in the page context, so it reuses the browser's TLS fingerprint + cookies.
# Direct httpx/curl_cffi requests get 1675004 (fingerprinted); this doesn't.
_FETCH_SCRIPT = """
async ({url, body, headers}) => {
    const r = await fetch(url, {
        method: "POST",
        headers: headers,
        body: body,
        credentials: "include",
    });
    return {status: r.status, text: await r.text()};
}
"""

_RATE_LIMIT_PAUSE = 30
_MAX_RATE_LIMIT_RETRIES = 3


async def _capture_pagination_context(page, url: str) -> dict:
    """Load url, scroll to fire the first pagination query, and grab its tokens/form/doc_id."""
    captured: dict = {}
    _, pagination_event = await _setup_token_capture(page, captured)
    await _load_and_scroll(page, url, pagination_event)

    try:
        await asyncio.wait_for(pagination_event.wait(), timeout=20.0)
    except asyncio.TimeoutError:
        raise RuntimeError(f"[fetch] no AdLibrarySearchPaginationQuery fired at {url}")

    if not captured.get("base_form_data"):
        raise RuntimeError("[fetch] pagination fired but base_form_data not captured")

    cookies = await page.context.cookies()
    captured["cookies"] = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    logger.info(f"[fetch] captured pagination context: {sorted(captured.keys())}")
    return captured


async def _fetch_page_in_browser(page, captured: dict, variables: dict) -> tuple[int, str]:
    """POST the GraphQL query from inside the page context."""
    from app.graphql_client import _build_form_data

    data = _build_form_data(captured, variables)
    body = urlencode(data)
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
        "x-fb-lsd": captured.get("lsd", ""),
        "x-asbd-id": "359341",
    }
    result = await page.evaluate(
        _FETCH_SCRIPT,
        {"url": "https://www.facebook.com/api/graphql/", "body": body, "headers": headers},
    )
    return result["status"], result["text"]


async def _page_fetch_attempt(url: str, max_ads: int) -> list[dict]:
    """One attempt: capture tokens, then paginate via in-page fetch. Raises _RateLimited if blocked."""
    from app.graphql_client import _parse_response_json, _extract_ads_and_cursor

    ad_nodes: list[dict] = []
    seen_ids: set[str] = set()

    async with browser_context() as context:
        page = await context.new_page()
        captured = await _capture_pagination_context(page, url)

        # reuse the exact variables FB sent (country, adType, sortData, first, v, ...)
        template = json.loads(captured["base_form_data"]["variables"])
        session_id = str(uuid.uuid4())
        cursor: str | None = None  # None = start from page 1, so we get the initial cards too
        page_num = 0
        rate_limit_hits = 0

        while len(ad_nodes) < max_ads:
            variables = {**template, "cursor": cursor, "sessionID": session_id}
            status, text = await _fetch_page_in_browser(page, captured, variables)
            page_num += 1

            if "1675004" in text:
                rate_limit_hits += 1
                if rate_limit_hits > _MAX_RATE_LIMIT_RETRIES:
                    raise _RateLimited(f"rate limited after {rate_limit_hits} hits (page {page_num})")
                logger.warning(
                    f"[fetch] page={page_num} rate limit 1675004 "
                    f"(hit {rate_limit_hits}/{_MAX_RATE_LIMIT_RETRIES}) — pause {_RATE_LIMIT_PAUSE}s"
                )
                await asyncio.sleep(_RATE_LIMIT_PAUSE)
                continue

            if status != 200:
                raise RuntimeError(f"[fetch] page={page_num} HTTP {status}: {text[:300]}")

            try:
                parsed = _parse_response_json(text)
            except Exception as exc:
                raise RuntimeError(f"[fetch] page={page_num} JSON parse error: {exc}\n{text[:400]}")

            nodes, next_cursor, has_next = _extract_ads_and_cursor(parsed)
            new_count = 0
            for node in nodes:
                nid = str(node.get("id") or "")
                if nid and nid not in seen_ids:
                    seen_ids.add(nid)
                    ad_nodes.append(node)
                    new_count += 1
            logger.info(
                f"[fetch] page={page_num} got={len(nodes)} new={new_count} "
                f"total={len(ad_nodes)} has_next={has_next}"
            )

            if not nodes and not next_cursor:
                logger.info("[fetch] empty page with no cursor — end of results")
                break
            cursor = next_cursor
            if not has_next or not cursor:
                logger.info(f"[fetch] pagination complete: {len(ad_nodes)} ads")
                break

            await asyncio.sleep(random.uniform(1.5, 3.5))

    logger.info(f"[fetch] done: {len(ad_nodes)} unique ads over {page_num} pages")
    return ad_nodes


async def scrape_via_page_fetch(
    url: str,
    max_ads: int | None = None,
) -> list[dict]:
    """
    Primary path: load once, paginate via in-page fetch() without scrolling.
    Flat RAM, no ~1700-card cap. On rate limit: rotate IP (verified) and retry.
    """
    from app.proxy import rotate_ip_verified

    if max_ads is None:
        max_ads = settings.max_ads_per_chunk
    for attempt in range(3):
        try:
            return await _page_fetch_attempt(url, max_ads)
        except _RateLimited as exc:
            if attempt >= 2:
                raise RuntimeError(f"IP rate limited after {attempt + 1} attempts with rotation") from exc
            logger.warning(f"[fetch] {exc} — rotating IP (attempt {attempt + 1}/3)")
            if not await rotate_ip_verified():
                raise RuntimeError("IP rate limited and rotation did not change the IP") from exc

    return []


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