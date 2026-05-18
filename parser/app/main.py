import asyncio
from loguru import logger
from app.config import settings


async def main():
    logger.info("Parser starting up")
    logger.info(f"DB: {settings.database_url}")
    logger.info(f"Proxy gateway: {settings.proxy_http_gateway}")
    logger.info(f"S3 bucket: {settings.s3_bucket}")

    import httpx
    async with httpx.AsyncClient(proxy=settings.proxy_http_gateway, timeout=30) as client:
        resp = await client.get("https://api.ipify.org")
        logger.info(f"Our IP via proxy: {resp.text}")

    logger.info("Parser idle, sleeping")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())