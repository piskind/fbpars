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
    return f"ads/{library_id[-2:]}/{library_id}/{kind}_{idx}.{ext}"


async def _download(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
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
        key = _s3_key(library_id, ext, idx, "img")

        try:
            img = Image.open(io.BytesIO(data))
            width, height = img.size
        except Exception:
            width, height = None, None

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
        data = await _download(url)
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