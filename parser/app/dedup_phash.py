import asyncio
import aioboto3
from loguru import logger
from sqlalchemy import select, func
from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Creative


async def main():
    async with AsyncSessionLocal() as session:
        groups_stmt = (
            select(Creative.phash)
            .where(Creative.phash.is_not(None))
            .group_by(Creative.phash)
            .having(func.count(Creative.id) > 1)
        )
        phashes = list((await session.execute(groups_stmt)).scalars().all())
        logger.info(f"Found {len(phashes)} phash groups with dupes")

        s3_keys_to_delete: list[str] = []
        total_dupes = 0

        for ph in phashes:
            cre_stmt = (
                select(Creative)
                .where(Creative.phash == ph)
                .order_by(Creative.id)
            )
            creatives = list((await session.execute(cre_stmt)).scalars().all())
            if len(creatives) < 2:
                continue

            canonical = creatives[0]
            dupes = creatives[1:]
            logger.info(f"phash={ph}: canonical id={canonical.id} ({canonical.s3_key}), {len(dupes)} dupes")

            for d in dupes:
                if d.s3_key and d.s3_key != canonical.s3_key:
                    s3_keys_to_delete.append(d.s3_key)
                d.s3_key = canonical.s3_key
                d.s3_url = canonical.s3_url
                total_dupes += 1

        await session.commit()
        logger.info(f"Updated {total_dupes} dupe creatives to point to canonical s3_keys")

    if s3_keys_to_delete:
        logger.info(f"Deleting {len(s3_keys_to_delete)} dupe objects from S3")
        session_factory = aioboto3.Session()
        async with session_factory.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        ) as s3:
            for key in s3_keys_to_delete:
                try:
                    await s3.delete_object(Bucket=settings.s3_bucket, Key=key)
                    logger.info(f"  deleted {key}")
                except Exception as e:
                    logger.warning(f"  failed to delete {key}: {e}")

    logger.info("Done")


if __name__ == "__main__":
    asyncio.run(main())