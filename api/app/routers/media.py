from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
import aioboto3
from loguru import logger
from app.config import settings

router = APIRouter(prefix="/api/media", tags=["media"])

VIDEO_EXTS = {"mp4", "mov", "webm", "avi", "mkv"}
PRESIGNED_TTL = 3600  # 1 час


async def _presigned_url(key: str) -> str:
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        verify=False,
    ) as s3:
        return await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": key},
            ExpiresIn=PRESIGNED_TTL,
        )


@router.get("/{key:path}")
async def get_media(key: str, request: Request):
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""

    # Видео: редиректим на presigned URL — браузер сам играет, range requests работают
    if ext in VIDEO_EXTS:
        try:
            url = await _presigned_url(key)
            return RedirectResponse(url=url, status_code=302)
        except Exception as e:
            logger.error(f"presigned url failed for key={key}: {type(e).__name__}: {e}")
            raise HTTPException(status_code=404, detail="not found")

    # Картинки: проксируем через api, поддерживая Range
    range_header = request.headers.get("Range")
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
            kwargs: dict = {"Bucket": settings.s3_bucket, "Key": key}
            if range_header:
                kwargs["Range"] = range_header
            obj = await s3.get_object(**kwargs)
            content_type = obj.get("ContentType", "application/octet-stream")
            data = await obj["Body"].read()
            headers = {
                "Cache-Control": "public, max-age=3600",
                "Accept-Ranges": "bytes",
            }
            if range_header and "ContentRange" in obj:
                headers["Content-Range"] = obj["ContentRange"]
            return Response(
                content=data,
                status_code=206 if range_header else 200,
                media_type=content_type,
                headers=headers,
            )
    except Exception as e:
        logger.error(f"S3 error for key={key}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=404, detail=f"{type(e).__name__}: {str(e)}")
