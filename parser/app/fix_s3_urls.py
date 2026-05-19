import asyncio
from urllib.parse import urlparse
from sqlalchemy import select, update
from app.db import AsyncSessionLocal
from app.models import Creative
from app.config import settings
from loguru import logger


def to_vhosted(old_url: str) -> str:
    parsed = urlparse(settings.s3_endpoint_url)
    prefix = f"{settings.s3_endpoint_url}/{settings.s3_bucket}/"
    if not old_url.startswith(prefix):
        return old_url
    key = old_url[len(prefix):]
    return f"{parsed.scheme}://{settings.s3_bucket}.{parsed.netloc}/{key}"


async def main():
    async with AsyncSessionLocal() as session:
        stmt = select(Creative)
        rows = (await session.execute(stmt)).scalars().all()
        count = 0
        for c in rows:
            if not c.s3_url:
                continue
            new_url = to_vhosted(c.s3_url)
            if new_url != c.s3_url:
                c.s3_url = new_url
                count += 1
        await session.commit()
        logger.info(f"Updated {count} creatives")


if __name__ == "__main__":
    asyncio.run(main())
