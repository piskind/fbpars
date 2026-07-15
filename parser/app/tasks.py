"""RQ job entry points (Phase 2).

RQ jobs are synchronous functions run in worker processes; each wraps the async worker
logic in its own event loop via asyncio.run. Job args are JSON-serialisable (ISO dates)
so they survive the Redis round-trip.
"""
import asyncio
from datetime import date


def _parse_date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _rotate_ip_on_retry() -> None:
    """On an RQ retry (a previous attempt failed — e.g. both GraphQL paths died), rotate
    to a fresh IP before re-attempting: repeated hits from the same IP to the same
    filter/date combo seem to make FB refuse AdLibrarySearchPaginationQuery.

    Retry detection uses a persisted attempt counter in job.meta (survives RQ re-runs and
    is stable across rq versions). No-ops when not running inside an RQ job.
    """
    try:
        from rq import get_current_job
    except Exception:
        return
    job = get_current_job()
    if job is None:
        return

    from loguru import logger
    try:
        attempt = int(job.meta.get("attempt", 0))
    except Exception:
        attempt = 0

    if attempt > 0:
        from app.proxy import rotate_ip
        logger.info(f"[parse_chunk] retry #{attempt} of {job.id} — rotating IP before re-attempt")
        try:
            asyncio.run(rotate_ip())
        except Exception as exc:
            logger.warning(f"[parse_chunk] pre-retry IP rotation failed: {exc}")

    job.meta["attempt"] = attempt + 1
    try:
        job.save_meta()
    except Exception:
        pass


def parse_chunk(
    config_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    cursor_start: str | None = None,
    run_id: int | None = None,
) -> dict:
    """RQ 'parse' queue job: process one (config, date-chunk). Returns the stats dict."""
    from app.worker import process_chunk
    _rotate_ip_on_retry()
    return asyncio.run(
        process_chunk(config_id, _parse_date(date_from), _parse_date(date_to), cursor_start)
    )


def process_media(
    ad_id: int,
    library_id: str,
    image_urls: list[str] | None = None,
    video_urls: list[str] | None = None,
    poster_urls: list[str] | None = None,
    config_id: int = 0,
) -> dict:
    """RQ 'media' queue job: download + phash-dedup + S3 for one ad's media set."""
    from app.media_worker import process_media_async
    return asyncio.run(
        process_media_async(ad_id, library_id, image_urls, video_urls, poster_urls, config_id)
    )
