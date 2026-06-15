import hashlib
import io
from pathlib import PurePosixPath
from urllib.parse import urlparse
import aioboto3
import httpx
from PIL import Image
import imagehash
from loguru import logger
from app.config import settings


def _ext_from_url(url: str, fallback: str) -> str:
    path = PurePosixPath(urlparse(url).path)
    ext = path.suffix.lower().lstrip(".")
    if ext in {"jpg", "jpeg", "png", "webp", "gif", "mp4", "mov", "webm"}:
        return ext
    return fallback

def _build_vhosted_url(endpoint_url: str, bucket: str, key: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(endpoint_url)
    return f"{parsed.scheme}://{bucket}.{parsed.netloc}/{key}"


def _s3_key(library_id: str, ext: str, idx: int, kind: str) -> str:
    return f"m/{library_id[-2:]}/{library_id}/{kind}_{idx}.{ext}"


_VIDEO_MAX_BYTES = 150 * 1024 * 1024  # 150 MB hard limit per video
_VIDEO_TIMEOUT   = 300                 # seconds — large files over mobile proxy


async def _download(url: str, timeout: int = 60, max_bytes: int | None = None) -> bytes | None:
    try:
        async with httpx.AsyncClient(
            proxy=settings.proxy_http_gateway,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                if max_bytes:
                    cl = resp.headers.get("content-length")
                    if cl and int(cl) > max_bytes:
                        mb = int(cl) // 1_048_576
                        logger.warning(f"Skip {url[:60]}: {mb} MB > limit {max_bytes // 1_048_576} MB")
                        return None
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(1024 * 256):
                    chunks.append(chunk)
                    total += len(chunk)
                    if max_bytes and total > max_bytes:
                        mb = total // 1_048_576
                        logger.warning(f"Skip {url[:60]}: exceeded {mb} MB during download")
                        return None
                return b"".join(chunks)
    except Exception as e:
        logger.warning(f"Download failed {url[:80]}: {e}")
        return None


def _compute_hashes(data: bytes, is_image: bool) -> tuple[str, str | None]:
    md5 = hashlib.md5(data).hexdigest()
    phash = None
    if is_image:
        try:
            img = Image.open(io.BytesIO(data))
            phash = str(imagehash.phash(img))
        except Exception as e:
            logger.warning(f"phash failed: {e}")
    return md5, phash


class MediaUploader:
    def __init__(self):
        self.session = aioboto3.Session()

    async def upload_image(self, library_id: str, url: str, idx: int) -> dict | None:
        data = await _download(url)
        if not data:
            return None
        md5, phash = _compute_hashes(data, is_image=True)
        ext = _ext_from_url(url, "jpg")
        try:
            img = Image.open(io.BytesIO(data))
            width, height = img.size
        except Exception:
            width, height = None, None

        if phash:
            from sqlalchemy import select
            from app.db import AsyncSessionLocal
            from app.models import Creative
            async with AsyncSessionLocal() as s:
                existing = (await s.execute(
                    select(Creative).where(Creative.phash == phash, Creative.s3_key.is_not(None)).limit(1)
                )).scalar_one_or_none()
            if existing:
                logger.info(f"phash dedupe: reusing {existing.s3_key} for {library_id}/img_{idx}")
                return {
                    "original_url": url,
                    "s3_key": existing.s3_key,
                    "s3_url": existing.s3_url,
                    "md5": md5,
                    "phash": phash,
                    "width": width,
                    "height": height,
                    "file_size": len(data),
                    "reused": True,
                }

        key = _s3_key(library_id, ext, idx, "img")
        async with self.session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        ) as s3:
            await s3.put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=data,
                ContentType=f"image/{ext if ext != 'jpg' else 'jpeg'}",
                ACL="public-read",
            )
        return {
            "original_url": url,
            "s3_key": key,
            "s3_url": _build_vhosted_url(settings.s3_endpoint_url, settings.s3_bucket, key),
            "md5": md5,
            "phash": phash,
            "width": width,
            "height": height,
            "file_size": len(data),
        }

    async def upload_video(self, library_id: str, url: str, idx: int) -> dict | None:
        data = await _download(url, timeout=_VIDEO_TIMEOUT, max_bytes=_VIDEO_MAX_BYTES)
        if not data:
            return None

        md5, _ = _compute_hashes(data, is_image=False)
        ext = _ext_from_url(url, "mp4")
        key = _s3_key(library_id, ext, idx, "vid")

        async with self.session.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        ) as s3:
            await s3.put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=data,
                ContentType=f"video/{ext}",
                ACL="public-read",
            )

        return {
            "original_url": url,
            "s3_key": key,
            "s3_url": _build_vhosted_url(settings.s3_endpoint_url, settings.s3_bucket, key),
            "md5": md5,
            "phash": None,
            "width": None,
            "height": None,
            "file_size": len(data),
        }