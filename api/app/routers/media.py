from fastapi import APIRouter, HTTPException, Response
import aioboto3
from loguru import logger
from app.config import settings


router = APIRouter(prefix="/api/media", tags=["media"])


@router.get("/{key:path}")
async def get_media(key: str):
    session = aioboto3.Session()
    try:
        async with session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            verify=False,
        ) as s3:
            obj = await s3.get_object(Bucket=settings.s3_bucket, Key=key)
            content_type = obj.get("ContentType", "application/octet-stream")
            data = await obj["Body"].read()

            return Response(
                content=data,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Accept-Ranges": "bytes",
                },
            )
    except Exception as e:
        logger.error(f"S3 error for key={key}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=404, detail=f"{type(e).__name__}: {str(e)}")