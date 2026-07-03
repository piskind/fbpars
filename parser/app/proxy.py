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
    """Rotate IP and confirm it changed — the rotate endpoint often 200s but keeps the same IP."""
    before = await current_ip()
    if not await rotate_ip():
        logger.warning("rotate_ip_verified: rotation trigger failed")
        return False
    after = await current_ip()

    if before is None or after is None:
        logger.warning(f"rotate_ip_verified: could not read IP (before={before}, after={after})")
        return False
    if before == after:
        logger.warning(f"rotate_ip_verified: IP unchanged ({after}) — proxy stuck")
        return False

    logger.info(f"rotate_ip_verified: IP changed {before} → {after}")
    return True