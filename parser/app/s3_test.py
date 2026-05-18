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
        logger.info(f"Bucket: {settings.s3_bucket}")

        test_key = "test/hello.txt"
        test_body = b"hello from fbparser"

        logger.info(f"Uploading {test_key}")
        await s3.put_object(Bucket=settings.s3_bucket, Key=test_key, Body=test_body)
        logger.info("Upload OK")

        logger.info(f"Downloading {test_key}")
        obj = await s3.get_object(Bucket=settings.s3_bucket, Key=test_key)
        body = await obj["Body"].read()
        logger.info(f"Downloaded: {body.decode()}")

        logger.info(f"Listing prefix test/")
        resp = await s3.list_objects_v2(Bucket=settings.s3_bucket, Prefix="test/")
        for item in resp.get("Contents", []):
            logger.info(f"  - {item['Key']} ({item['Size']} bytes)")

        logger.info(f"Deleting {test_key}")
        await s3.delete_object(Bucket=settings.s3_bucket, Key=test_key)
        logger.info("Delete OK")

        logger.info("S3 smoke-test passed")


if __name__ == "__main__":
    asyncio.run(main())
