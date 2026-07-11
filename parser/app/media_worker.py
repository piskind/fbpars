"""Media worker (Phase 4).

Runs on the dedicated "media" RQ queue, decoupled from pagination. Reuses the existing
upload + phash-dedup logic in worker._upload_card_media (which honours SKIP_VIDEO_FIRST_PASS
and logs downloaded traffic), so the dedup/removal semantics stay identical.

Scaled separately from parser-workers via MEDIA_WORKERS.
"""
from loguru import logger

from app.storage import MediaUploader
from app.parsers.library_card import ParsedCard


async def process_media_async(
    ad_id: int,
    library_id: str,
    image_urls: list[str] | None = None,
    video_urls: list[str] | None = None,
    poster_urls: list[str] | None = None,
    config_id: int = 0,
) -> dict:
    # Import here to avoid a heavy import chain at module load in the RQ worker.
    from app.worker import _upload_card_media

    card = ParsedCard(
        library_id=library_id,
        image_urls=list(image_urls or []),
        video_urls=list(video_urls or []),
        poster_urls=list(poster_urls or []),
    )
    uploader = MediaUploader()
    stats = await _upload_card_media(uploader, config_id, ad_id, card)
    logger.info(f"[media] ad={ad_id} {library_id}: {stats}")
    return stats
