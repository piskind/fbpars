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