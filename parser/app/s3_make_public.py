import asyncio
import aioboto3
from app.config import settings
from loguru import logger


async def main():
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    ) as s3:
        paginator = s3.get_paginator("list_objects_v2")
        count = 0
        failed = 0
        async for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix="ads/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                try:
                    await s3.copy_object(
                        Bucket=settings.s3_bucket,
                        Key=key,
                        CopySource={"Bucket": settings.s3_bucket, "Key": key},
                        ACL="public-read",
                        Metadata={"acl-fix": "1"},
                        MetadataDirective="REPLACE",
                    )
                    count += 1
                    if count % 10 == 0:
                        logger.info(f"Processed {count} objects")
                except Exception as e:
                    logger.warning(f"Failed for {key}: {e}")
                    failed += 1
        logger.info(f"Done. Success: {count}, failed: {failed}")


if __name__ == "__main__":
    asyncio.run(main())