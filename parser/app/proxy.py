import asyncio
import httpx
from loguru import logger
from app.config import settings


async def rotate_ip() -> bool:
    if not settings.proxy_rotate_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(settings.proxy_rotate_url)
            resp.raise_for_status()
            logger.info(f"IP rotation triggered: {resp.text[:100]}")
        await asyncio.sleep(settings.proxy_rotate_wait_sec)
        return True
    except Exception as e:
        logger.warning(f"IP rotation failed: {e}")
        return False


async def current_ip() -> str | None:
    try:
        async with httpx.AsyncClient(proxy=settings.proxy_http_gateway, timeout=15) as client:
            resp = await client.get("https://api.ipify.org")
            return resp.text.strip()
    except Exception as e:
        logger.warning(f"IP check failed: {e}")
        return None


async def rotate_ip_verified() -> bool:
    """
    Rotate proxy IP and verify it actually changed.

    The proxy provider's rotate endpoint sometimes returns 200 but keeps the same
    egress IP. Callers can't rely on rotate_ip() alone. This snapshots the IP
    before/after and returns True only if it genuinely changed.
    """
    before = await current_ip()
    triggered = await rotate_ip()
    if not triggered:
        logger.warning("rotate_ip_verified: rotation trigger failed")
        return False
    after = await current_ip()

    if before is None or after is None:
        # Can't confirm — treat as unverified rather than claim success.
        logger.warning(
            f"rotate_ip_verified: could not read IP (before={before}, after={after})"
        )
        return False
    if before == after:
        logger.warning(f"rotate_ip_verified: IP unchanged after rotation ({after}) — proxy stuck")
        return False

    logger.info(f"rotate_ip_verified: IP changed {before} → {after}")
    return True