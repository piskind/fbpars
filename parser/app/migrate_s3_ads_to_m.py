"""
Скрипт миграции S3: переименовывает ads/ → m/ для всех объектов,
обновляет s3_key и s3_url в таблице creatives.

Запуск: python -m app.migrate_s3_ads_to_m
"""
import asyncio
import re
from loguru import logger
import aioboto3
from sqlalchemy import select, update

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Creative
from app.storage import _build_vhosted_url


OLD_PREFIX = "ads/"
NEW_PREFIX = "m/"


async def list_old_objects(s3) -> list[str]:
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    async for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=OLD_PREFIX):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


async def copy_and_delete(s3, old_key: str, new_key: str):
    copy_source = {"Bucket": settings.s3_bucket, "Key": old_key}
    await s3.copy_object(
        CopySource=copy_source,
        Bucket=settings.s3_bucket,
        Key=new_key,
        ACL="public-read",
    )
    await s3.delete_object(Bucket=settings.s3_bucket, Key=old_key)


async def update_db(old_key: str, new_key: str):
    new_url = _build_vhosted_url(settings.s3_endpoint_url, settings.s3_bucket, new_key)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Creative)
            .where(Creative.s3_key == old_key)
            .values(s3_key=new_key, s3_url=new_url)
        )
        await session.commit()


async def migrate():
    session_factory = aioboto3.Session()
    async with session_factory.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    ) as s3:
        keys = await list_old_objects(s3)
        logger.info(f"Found {len(keys)} objects under '{OLD_PREFIX}'")

        for old_key in keys:
            new_key = NEW_PREFIX + old_key[len(OLD_PREFIX):]
            logger.info(f"  {old_key}  →  {new_key}")
            try:
                await copy_and_delete(s3, old_key, new_key)
                await update_db(old_key, new_key)
                logger.info(f"  OK")
            except Exception as e:
                logger.error(f"  FAILED: {e}")

    logger.info("Migration complete")


if __name__ == "__main__":
    asyncio.run(migrate())
