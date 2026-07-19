import re
import socket
import asyncio
from urllib.parse import urlparse
from loguru import logger

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
    HAS_LANGDETECT = True
except ImportError:
    HAS_LANGDETECT = False


APP_STORE_PATTERNS = {
    "ios": [r"apps\.apple\.com", r"itunes\.apple\.com"],
    "android": [r"play\.google\.com/store"],
    "huawei": [r"appgallery\.huawei\.com"],
}

ECOM_PLATFORM_PATTERNS = {
    "shopify": [r"\.myshopify\.com", r"cdn\.shopify\.com"],
    "wordpress": [r"\.wordpress\.com", r"/wp-content/", r"/wp-includes/"],
    "wix": [r"\.wixsite\.com", r"\.wix\.com"],
    "tilda": [r"\.tilda\.ws", r"tildacdn\.com"],
    "woocommerce": [r"/wc-api/", r"woocommerce"],
    "magento": [r"magento", r"/static/version"],
    "bigcommerce": [r"\.mybigcommerce\.com"],
    "squarespace": [r"\.squarespace\.com"],
    "webflow": [r"\.webflow\.io"],
    "landingi": [r"\.landingi\.com"],
}


def detect_app_store(link_url: str | None) -> str | None:
    if not link_url:
        return None
    low = link_url.lower()
    for label, patterns in APP_STORE_PATTERNS.items():
        if any(re.search(p, low) for p in patterns):
            return label
    return None


def detect_ecom_platform(link_url: str | None) -> str | None:
    if not link_url:
        return None
    low = link_url.lower()
    for label, patterns in ECOM_PLATFORM_PATTERNS.items():
        if any(re.search(p, low) for p in patterns):
            return label
    return None


def detect_language(text: str | None) -> str | None:
    if not text or not HAS_LANGDETECT:
        return None
    cleaned = re.sub(r"http[s]?://\S+", " ", text)
    cleaned = re.sub(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F900-\U0001F9FF☀-⛿✀-➿]+", " ", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) < 10:
        return None
    try:
        return detect(cleaned)
    except Exception as e:
        logger.debug(f"langdetect failed: {e}")
        return None


# Host → resolved IP (or None for "known unresolvable"). Bounds runaway DNS work: dead
# advertiser domains ("No address associated with hostname") were re-resolved for every
# card and each lookup blocked the batch commit until the resolver timed out. The cache
# collapses repeats to O(1) and the timeout caps any single lookup.
_DNS_CACHE: dict[str, str | None] = {}
_DNS_CACHE_MAX = 5000
_DNS_TIMEOUT_SEC = 2.0


async def resolve_ip(link_url: str | None) -> str | None:
    if not link_url:
        return None
    host = urlparse(link_url).hostname
    if not host:
        return None
    if host in _DNS_CACHE:
        return _DNS_CACHE[host]

    ip: str | None = None
    try:
        loop = asyncio.get_running_loop()
        # Hard timeout so one slow/dead domain can't stall the whole batch on the resolver.
        ip = await asyncio.wait_for(
            loop.run_in_executor(None, socket.gethostbyname, host),
            timeout=_DNS_TIMEOUT_SEC,
        )
    except Exception as e:
        # Cache the failure too — a dead domain stays dead for this run's lifetime.
        logger.debug(f"resolve_ip failed for {link_url}: {e}")
        ip = None

    if len(_DNS_CACHE) < _DNS_CACHE_MAX:
        _DNS_CACHE[host] = ip
    return ip


async def enrich_ad_fields(link_url: str | None, body: str | None) -> dict:
    app_store = detect_app_store(link_url)
    ecom_platform = detect_ecom_platform(link_url)
    language = detect_language(body)
    ip = await resolve_ip(link_url)
    return {
        "app_store": app_store,
        "ecom_platform": ecom_platform,
        "language": language,
        "ip": ip,
    }
