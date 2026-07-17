import asyncio
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models import Ad, Creative
from loguru import logger
from app.db import AsyncSessionLocal
from app.models import ParsingConfig, AdMediaType, ChunkProgress, chunk_key
from app.browser import (
    browser_context,
    build_library_url,
    goto_with_challenge_retry,
    scrape_via_browser_graphql,
    scrape_via_page_fetch,
)
from app.parsers.library_card import parse_card_text
from app.parsers.library_extractor import scroll_and_collect
from app.graphql_client import map_graphql_card
from app.graphql_paginator import paginate
from app.repository import upsert_ad, save_creative
from app.storage import MediaUploader
from app.proxy import rotate_ip, current_ip
from app.config import settings

# Shared across all concurrent configs if run_once ever parallelises process_config calls.
# Currently one config runs at a time, so this is effectively per-config.
MEDIA_SEMAPHORE = asyncio.Semaphore(10)


async def get_active_configs(session, config_id: int | None = None) -> list[ParsingConfig]:
    if config_id is not None:
        cfg = await session.get(ParsingConfig, config_id)
        return [cfg] if cfg else []
    stmt = select(ParsingConfig).where(ParsingConfig.is_active.is_(True)).order_by(ParsingConfig.id)
    return list((await session.execute(stmt)).scalars().all())


async def _upload_card_media(
    uploader: MediaUploader,
    config_id: int,
    ad_id: int,
    card,
) -> dict:
    s = {"media_ok": 0, "media_fail": 0, "removed_no_media": 0, "skipped_phash_duplicate": 0}
    all_reused = True
    img_uploads: list[dict] = []
    vid_uploads: list[dict] = []
    fail_count = 0

    async with MEDIA_SEMAPHORE:
        for idx, img_url in enumerate(card.image_urls[:3]):
            upload = await uploader.upload_image(card.library_id, img_url, idx)
            if upload:
                img_uploads.append(upload)
            else:
                fail_count += 1

        # Video is the heaviest traffic and the server has a transfer cap — skip it on the
        # first pass by default (SKIP_VIDEO_FIRST_PASS). Poster still represents the ad.
        if settings.skip_video_first_pass:
            if card.video_urls:
                s["video_skipped"] = len(card.video_urls[:2])
        else:
            for idx, vid_url in enumerate(card.video_urls[:2]):
                upload = await uploader.upload_video(card.library_id, vid_url, idx)
                if upload:
                    vid_uploads.append(upload)
                else:
                    fail_count += 1

        # Fall back to poster images when there are no images AND we didn't just save videos.
        if not card.image_urls and not vid_uploads:
            for idx, poster_url in enumerate(card.poster_urls[:2]):
                upload = await uploader.upload_image(card.library_id, poster_url, idx)
                if upload:
                    img_uploads.append(upload)
                else:
                    fail_count += 1

    s["media_fail"] = fail_count
    media_saved = len(img_uploads) + len(vid_uploads)
    s["media_ok"] = media_saved

    # Track real downloaded traffic (phash-reused uploads cost nothing) — server has a cap.
    downloaded_bytes = sum(
        u.get("file_size") or 0
        for u in (img_uploads + vid_uploads)
        if not u.get("reused")
    )
    s["bytes_downloaded"] = downloaded_bytes
    if downloaded_bytes:
        logger.info(f"[#{config_id}] {card.library_id}: downloaded {downloaded_bytes / 1_048_576:.1f} MB")

    for upload in img_uploads:
        if not upload.get("reused"):
            all_reused = False
    if vid_uploads:
        all_reused = False  # videos are never reused

    async with AsyncSessionLocal() as session:
        for upload in img_uploads:
            await save_creative(session, ad_id, AdMediaType.IMAGE, upload)
        for upload in vid_uploads:
            await save_creative(session, ad_id, AdMediaType.VIDEO, upload)
        await session.flush()

        if all_reused and media_saved > 0:
            # All creatives reuse existing S3 keys — no new uploads, but keep the ad record.
            # A new library_id is still a distinct ad even if visuals are identical.
            s["skipped_phash_duplicate"] += 1
            logger.info(f"[#{config_id}] phash-reuse {card.library_id}: saved with existing S3 keys")

        await session.commit()

        cnt = await session.scalar(
            select(func.count(Creative.id))
            .where(Creative.ad_id == ad_id, Creative.s3_url.is_not(None))
        )
        if not cnt:
            db_ad = await session.get(Ad, ad_id)
            if db_ad:
                await session.delete(db_ad)
                await session.commit()
                logger.info(f"[#{config_id}] removed {card.library_id}: no media saved")
                s["removed_no_media"] += 1

    return s


def split_date_range(date_from: date, date_to: date, chunk_days: int = 1) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    current = date_from
    while current <= date_to:
        chunk_end = min(current + timedelta(days=chunk_days - 1), date_to)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


_EMPTY_STATS = {
    "raw": 0, "new": 0, "updated": 0, "urls_saved": 0, "media_ok": 0, "media_fail": 0,
    "errors": 0, "skipped_duplicate": 0, "skipped_no_media": 0,
    "skipped_already_rejected": 0, "skipped_phash_duplicate": 0, "removed_no_media": 0,
}


async def _upsert_cards_and_collect_media(
    cards,
    config: ParsingConfig,
    period_tag: str,
) -> tuple[dict, list]:
    """Phase 1: sequential DB upserts. Returns (stats_delta, media_tasks)."""
    stats = dict(_EMPTY_STATS)
    seen_ids: set[str] = set()
    media_tasks: list[tuple[int, object]] = []

    for card in cards:
        if not card.library_id:
            continue
        if card.library_id in seen_ids:
            stats["skipped_duplicate"] += 1
            continue
        seen_ids.add(card.library_id)

        if not card.image_urls and not card.video_urls and not card.poster_urls:
            logger.info(f"[#{config.id}]{period_tag} skip {card.library_id}: no media")
            stats["skipped_no_media"] += 1
            continue

        async with AsyncSessionLocal() as session:
            try:
                ad, is_new, skipped = await upsert_ad(
                    session, card, config.country, config.keyword, config.vertical, config.config_type
                )
                await session.commit()
            except Exception as e:
                logger.warning(f"[#{config.id}]{period_tag} DB error for {card.library_id}: {e}")
                await session.rollback()
                stats["errors"] += 1
                continue

        if is_new:
            stats["new"] += 1
        elif skipped:
            stats["skipped_already_rejected"] += 1
        else:
            stats["updated"] += 1

        if is_new and not skipped:
            # Direct FB CDN URLs are already persisted by upsert_ad — nothing to download.
            if card.image_urls or card.video_urls:
                stats["urls_saved"] += 1
            # Legacy S3 download/phash pipeline runs only when explicitly re-enabled.
            if settings.enable_media_download:
                media_tasks.append((ad.id, card))

    return stats, media_tasks


async def _run_media_phase(
    media_tasks,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str,
) -> dict:
    """Phase 2: parallel media uploads. Returns stats_delta."""
    stats = {}
    logger.info(f"[#{config.id}]{period_tag} starting parallel media for {len(media_tasks)} new ads")
    results = await asyncio.gather(
        *[_upload_card_media(uploader, config.id, ad_id, card) for ad_id, card in media_tasks],
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[#{config.id}]{period_tag} media task error: {r}")
            stats["errors"] = stats.get("errors", 0) + 1
        else:
            for k, v in r.items():
                stats[k] = stats.get(k, 0) + v
    return stats


async def _dispatch_media(
    media_tasks,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str,
) -> dict:
    """Phase 4: either enqueue one media job per ad (split pipeline) or upload inline.

    In the split path, metadata is already committed; media (image + phash-dedup, video
    deferred by SKIP_VIDEO_FIRST_PASS) runs on the dedicated media queue / media-worker.
    """
    if not media_tasks:
        return {}

    if settings.split_media_pipeline and settings.use_queue:
        from app.queue import media_queue
        from app.tasks import process_media
        from rq.job import Job

        q = media_queue()
        conn = q.connection
        enqueued = 0
        for ad_id, card in media_tasks:
            jid = f"media:{ad_id}"
            if Job.exists(jid, connection=conn):
                continue
            q.enqueue(
                process_media,
                kwargs={
                    "ad_id": ad_id,
                    "library_id": card.library_id,
                    "image_urls": list(card.image_urls or []),
                    "video_urls": list(card.video_urls or []),
                    "poster_urls": list(card.poster_urls or []),
                    "config_id": config.id,
                },
                job_id=jid,
                job_timeout=settings.media_job_timeout,
                result_ttl=1800,
                failure_ttl=86400,
            )
            enqueued += 1
        logger.info(f"[#{config.id}]{period_tag} enqueued {enqueued} media job(s)")
        return {"media_enqueued": enqueued}

    return await _run_media_phase(media_tasks, config, uploader, period_tag)



async def _preload_seen_ids(
    country: str, date_from: date | None, date_to: date | None
) -> set[str]:
    """Library IDs already saved for this chunk's date range, so a resumed or drifted
    cursor doesn't waste time re-processing cards already in the DB. Seeded into the
    paginator's dedup set (node id == ad_archive_id == library_id)."""
    stmt = select(Ad.library_id).where(func.upper(Ad.country) == country.upper())
    if date_from is not None:
        stmt = stmt.where(Ad.started_at >= date_from)
    if date_to is not None:
        # inclusive of date_to's whole day (handles single-day chunks where from == to)
        stmt = stmt.where(Ad.started_at < date_to + timedelta(days=1))
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return {r for r in rows if r}


async def _upsert_chunk_progress(
    session,
    config_id: int,
    date_from: date | None,
    date_to: date | None,
    cursor: str | None,
    has_next: bool,
    saved: int,
) -> None:
    """Bookmark pagination progress for this (config, date-chunk) in the SAME transaction
    as the batch's cards, so the cursor never runs ahead of what's saved."""
    df, dt = chunk_key(date_from, date_to)
    stmt = pg_insert(ChunkProgress).values(
        config_id=config_id, date_from=df, date_to=dt,
        last_cursor=cursor, collected_count=saved, has_next=has_next,
    ).on_conflict_do_update(
        index_elements=["config_id", "date_from", "date_to"],
        set_={
            "last_cursor": cursor,
            "collected_count": ChunkProgress.collected_count + saved,
            "has_next": has_next,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def _scrape_single_period_graphql(
    url: str,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str = "",
    cursor_start: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    track_chunk: bool = False,
) -> dict:
    """GraphQL path: browser-less pagination (Phase 1) with legacy browser paths as fallback."""
    stats = dict(_EMPTY_STATS)

    async def _persist_batch(nodes: list[dict], cursor: str | None, has_next: bool) -> None:
        """Map + upsert one batch and bookmark the cursor in a single transaction, so
        every committed batch survives a later crash and the cursor stays in lockstep with
        saved cards. One begin_nested savepoint per card isolates a bad row from the batch;
        upsert_ad already persists the direct-media URLs. Stats reflect what's in the DB."""
        cards = [map_graphql_card(node) for node in nodes]
        media_tasks: list[tuple[int, object]] = []
        saved = 0
        batch_seen: set[str] = set()

        async with AsyncSessionLocal() as session:
            for card in cards:
                if not card.library_id or card.library_id in batch_seen:
                    if card.library_id:
                        stats["skipped_duplicate"] += 1
                    continue
                batch_seen.add(card.library_id)
                if not card.image_urls and not card.video_urls and not card.poster_urls:
                    stats["skipped_no_media"] += 1
                    continue
                try:
                    async with session.begin_nested():  # savepoint: one bad card can't sink the batch
                        ad, is_new, skipped = await upsert_ad(
                            session, card, config.country, config.keyword,
                            config.vertical, config.config_type,
                        )
                except Exception as e:
                    logger.warning(f"[#{config.id}]{period_tag} DB error for {card.library_id}: {e}")
                    stats["errors"] += 1
                    continue

                if is_new:
                    stats["new"] += 1
                    saved += 1
                elif skipped:
                    stats["skipped_already_rejected"] += 1
                else:
                    stats["updated"] += 1
                    saved += 1

                if is_new and not skipped:
                    if card.image_urls or card.video_urls:
                        stats["urls_saved"] += 1
                    if settings.enable_media_download:
                        media_tasks.append((ad.id, card))

            await session.commit()  # cards land first — independent of the bookmark below

        # Bookmark the cursor AFTER the cards are committed (so it can never point past
        # saved data) and in its OWN transaction, so a chunk_progress failure — e.g. the
        # table is missing because the deploy didn't run the migration — can't roll back
        # the batch of ads we just saved. Worst case the bookmark lags and the next run
        # re-collects a little (idempotent via upsert), which is the safe direction.
        if track_chunk:
            try:
                async with AsyncSessionLocal() as session:
                    await _upsert_chunk_progress(
                        session, config.id, date_from, date_to, cursor, has_next, saved
                    )
                    await session.commit()
            except Exception as e:
                logger.warning(f"[#{config.id}]{period_tag} chunk_progress bookmark failed (cards saved): {e}")

        stats["raw"] += len(nodes)
        phase2 = await _dispatch_media(media_tasks, config, uploader, period_tag)
        for k, v in phase2.items():
            stats[k] = stats.get(k, 0) + v
        logger.info(
            f"[#{config.id}]{period_tag} committed batch: +{saved} cards "
            f"(saved so far: new={stats['new']} updated={stats['updated']} raw={stats['raw']}) "
            f"has_next={has_next}"
        )

    if settings.graphql_mode == "fetch":
        logger.info(
            f"[#{config.id}]{period_tag} starting browser-less pagination "
            f"(mode={settings.pagination_mode}, commit_batch={settings.commit_batch_size}, "
            f"resume_cursor={'yes' if cursor_start else 'no'})"
        )
        seen_ids: set[str] | None = None
        if track_chunk:
            seen_ids = await _preload_seen_ids(config.country, date_from, date_to)
            if seen_ids:
                logger.info(f"[#{config.id}]{period_tag} preloaded {len(seen_ids)} saved ids into dedup")
        # Incremental + resumable: paginate() hands each commit_batch_size batch to
        # _persist_batch (cards + cursor bookmark), keeps only the current batch in memory.
        await paginate(url, start_cursor=cursor_start, on_batch=_persist_batch, seen_ids=seen_ids)
        return stats

    # Legacy browser paths (non-default modes) still collect-then-upsert in one shot.
    if settings.graphql_mode == "page_fetch":
        logger.info(f"[#{config.id}]{period_tag} starting browser-GraphQL scrape (in-page fetch pagination)")
        raw_nodes = await scrape_via_page_fetch(url)
    else:
        logger.info(f"[#{config.id}]{period_tag} starting browser-GraphQL scrape (response interception)")
        raw_nodes = await scrape_via_browser_graphql(url)
    await _persist_batch(raw_nodes, None, False)
    return stats


async def _scrape_single_period_playwright(
    url: str,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str = "",
) -> dict:
    """Playwright scroll path (fallback)."""
    stats = dict(_EMPTY_STATS)
    try:
        async with browser_context() as context:
            page = await context.new_page()
            loaded = await goto_with_challenge_retry(page, url)
            if not loaded:
                logger.warning(f"[#{config.id}]{period_tag} __rd_verify challenge persisted, skipping")
                return stats

            try:
                await page.wait_for_selector('div:has-text("Library ID")', timeout=20_000)
            except Exception:
                logger.warning(f"[#{config.id}]{period_tag} No 'Library ID' on page, probably empty results")
                return stats

            await asyncio.sleep(5)
            max_scrolls = 40 if config.config_type in ("filters", "fanpage") else 80
            raw_cards = await asyncio.wait_for(
                scroll_and_collect(page, max_scrolls=max_scrolls, stable_rounds=7),
                timeout=600,
            )
            stats["raw"] = len(raw_cards)

        cards = []
        for raw in raw_cards:
            card = parse_card_text(raw["text"])
            card.image_urls = [img["src"] for img in raw["images"]]
            card.video_urls = [v["src"] for v in raw["videos"] if v["src"]]
            card.poster_urls = [v["poster"] for v in raw["videos"] if v["poster"]]
            card.page_url = raw["page_url"]
            card.link_url = raw["external_url"]
            cards.append(card)

        phase1, media_tasks = await _upsert_cards_and_collect_media(cards, config, period_tag)
        for k, v in phase1.items():
            stats[k] = stats.get(k, 0) + v

        phase2 = await _dispatch_media(media_tasks, config, uploader, period_tag)
        for k, v in phase2.items():
            stats[k] = stats.get(k, 0) + v

    except Exception as exc:
        logger.error(f"[#{config.id}]{period_tag} Fatal Playwright error: {exc}")
        stats["errors"] += 1

    return stats


async def _scrape_single_period(
    url: str,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str = "",
    cursor_start: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    track_chunk: bool = False,
) -> dict:
    """Dispatch to GraphQL or Playwright path based on settings.use_graphql.

    When use_graphql is on, a GraphQL failure RAISES (the chunk job fails and RQ retries
    with a fresh IP) instead of silently falling back to the DOM-scroll path — that
    fallback is capped by max_scrolls + FB's DOM virtualisation and returns ~500 cards
    where GraphQL returns thousands, so reporting it as a "success" hides a degraded run.
    _scrape_single_period_playwright is kept intact and still used when use_graphql=False
    (explicit Playwright mode).
    """
    if settings.use_graphql:
        try:
            return await _scrape_single_period_graphql(
                url, config, uploader, period_tag, cursor_start, date_from, date_to, track_chunk
            )
        except Exception as exc:
            logger.error(
                f"[#{config.id}]{period_tag} GraphQL path failed: {exc} — failing chunk "
                f"(no silent DOM-scroll fallback); RQ will retry with a fresh IP"
            )
            raise RuntimeError(
                f"GraphQL scrape failed for config #{config.id}{period_tag}: {exc}"
            ) from exc
    return await _scrape_single_period_playwright(url, config, uploader, period_tag)


async def process_config(config: ParsingConfig, uploader: MediaUploader) -> dict:
    effective_date_from = config.date_from
    if config.auto_date_from_last_parse and config.last_parsed_at:
        effective_date_from = config.last_parsed_at.date()

    ad_type = (config.category or "all") if config.config_type in ("filters", "fanpage") else "all"

    # Date-range splitting: filters/fanpage configs with a multi-day period get split into
    # daily sub-queries to bypass FB's ~1700-card scroll cap per request.
    use_date_split = (
        config.config_type in ("filters", "fanpage")
        and effective_date_from is not None
        and config.date_to is not None
        and config.date_to > effective_date_from
    )

    total_stats = {
        "raw": 0, "new": 0, "updated": 0, "media_ok": 0, "media_fail": 0, "errors": 0,
        "skipped_duplicate": 0, "skipped_no_media": 0, "skipped_already_rejected": 0,
        "skipped_phash_duplicate": 0, "removed_no_media": 0,
    }

    if use_date_split:
        chunks = split_date_range(effective_date_from, config.date_to, chunk_days=30)
        logger.info(
            f"[#{config.id}] date-range split: {len(chunks)} monthly sub-periods "
            f"({effective_date_from} → {config.date_to})"
        )
        for i, (chunk_from, chunk_to) in enumerate(chunks, 1):
            period_tag = f" [month {i}/{len(chunks)} {chunk_from}]"
            url = build_library_url(
                config.country, config.keyword, config.languages,
                active_status=config.active_status or "all",
                media_type=config.media_type_filter or "all",
                platforms=config.platforms,
                date_from=chunk_from,
                date_to=chunk_to,
                advertiser=config.advertiser,
                ad_type=ad_type,
                sort_mode=getattr(config, "sort_mode", "total_impressions") or "total_impressions",
                sort_direction=getattr(config, "sort_direction", "desc") or "desc",
                is_targeted_country=getattr(config, "is_targeted_country", None),
            )
            logger.info(f"[#{config.id}] sub-period {i}/{len(chunks)}: {chunk_from} → {url}")
            chunk_stats = await _scrape_single_period(url, config, uploader, period_tag=period_tag)
            for k, v in chunk_stats.items():
                total_stats[k] = total_stats.get(k, 0) + v
            logger.info(
                f"[#{config.id}] sub-period {i}/{len(chunks)} done: "
                f"raw={chunk_stats['raw']} new={chunk_stats['new']} "
                f"total_so_far new={total_stats['new']}"
            )
            if i < len(chunks):
                pause = 3 if chunk_stats["raw"] == 0 else 12
                await asyncio.sleep(pause)
    else:
        url = build_library_url(
            config.country, config.keyword, config.languages,
            active_status=config.active_status or "all",
            media_type=config.media_type_filter or "all",
            platforms=config.platforms,
            date_from=effective_date_from,
            date_to=config.date_to,
            advertiser=config.advertiser,
            ad_type=ad_type,
            sort_mode=getattr(config, "sort_mode", "total_impressions") or "total_impressions",
            sort_direction=getattr(config, "sort_direction", "desc") or "desc",
            is_targeted_country=getattr(config, "is_targeted_country", None),
        )
        kw_tag = config.keyword or "(no keyword)"
        lang_tag = f" lang={config.languages}" if config.languages else ""
        type_tag = f" [{config.config_type}]"
        logger.info(f"[#{config.id}]{type_tag} {kw_tag}/{config.country}{lang_tag} → {url}")
        total_stats = await _scrape_single_period(url, config, uploader)

    # Stamp last_parsed_at so auto_date_from_last_parse advances on next run
    async with AsyncSessionLocal() as session:
        cfg = await session.get(ParsingConfig, config.id)
        if cfg:
            cfg.last_parsed_at = datetime.now(timezone.utc)
            await session.commit()

    logger.info(f"[#{config.id}] DONE: {total_stats}")
    return total_stats


def _build_url_for_config(config: ParsingConfig, date_from: date | None, date_to: date | None) -> str:
    """Build the Ad Library URL for one config over one date chunk (shared by run_once & chunk jobs)."""
    ad_type = (config.category or "all") if config.config_type in ("filters", "fanpage") else "all"
    return build_library_url(
        config.country, config.keyword, config.languages,
        active_status=config.active_status or "all",
        media_type=config.media_type_filter or "all",
        platforms=config.platforms,
        date_from=date_from,
        date_to=date_to,
        advertiser=config.advertiser,
        ad_type=ad_type,
        sort_mode=getattr(config, "sort_mode", "total_impressions") or "total_impressions",
        sort_direction=getattr(config, "sort_direction", "desc") or "desc",
        is_targeted_country=getattr(config, "is_targeted_country", None),
    )


async def process_chunk(
    config_id: int,
    date_from: date | None,
    date_to: date | None,
    cursor_start: str | None = None,
) -> dict:
    """Process ONE (config, date-chunk) unit — the RQ job body (Phase 2).

    Self-contained: own DB session, own MediaUploader. Idempotent via upsert_ad, so a
    crashed chunk can be safely re-run without restarting the whole geo.
    """
    async with AsyncSessionLocal() as session:
        config = await session.get(ParsingConfig, config_id)
    if config is None:
        logger.error(f"[chunk] config #{config_id} not found")
        return {"error": f"config {config_id} not found"}

    uploader = MediaUploader()
    url = _build_url_for_config(config, date_from, date_to)
    period_tag = f" [{date_from}..{date_to}]" if date_from or date_to else ""
    logger.info(f"[#{config_id}]{period_tag} chunk start → {url}")

    stats = await _scrape_single_period(
        url, config, uploader, period_tag, cursor_start,
        date_from=date_from, date_to=date_to, track_chunk=True,
    )

    async with AsyncSessionLocal() as session:
        cfg = await session.get(ParsingConfig, config_id)
        if cfg:
            cfg.last_parsed_at = datetime.now(timezone.utc)
            await session.commit()

    logger.info(f"[#{config_id}]{period_tag} chunk done: {stats}")
    return stats


CONFIG_CONCURRENCY = 3


async def run_once(
    limit: int | None = None,
    config_id: int | None = None,
    parallel: bool = True,
) -> None:
    ip = await current_ip()
    logger.info(f"Starting worker. Current IP: {ip}")

    async with AsyncSessionLocal() as session:
        configs = await get_active_configs(session, config_id=config_id)

    if not configs and config_id is not None:
        logger.error(f"Config #{config_id} not found")
        return

    if limit:
        configs = configs[:limit]

    mode = f"parallel (concurrency={CONFIG_CONCURRENCY})" if parallel else "sequential"
    logger.info(f"Loaded {len(configs)} config(s) — mode: {mode}{f' (single: #{config_id})' if config_id else ''}")

    uploader = MediaUploader()
    total = {
        "raw": 0, "new": 0, "updated": 0, "media_ok": 0, "media_fail": 0, "errors": 0,
        "skipped_duplicate": 0, "skipped_no_media": 0, "skipped_already_rejected": 0,
        "skipped_phash_duplicate": 0, "removed_no_media": 0,
    }

    if parallel and not config_id:
        sem = asyncio.Semaphore(CONFIG_CONCURRENCY)

        async def _run_with_sem(config: ParsingConfig) -> dict:
            async with sem:
                logger.info(f"[parallel] starting config #{config.id} ({config.keyword or 'no-kw'}/{config.country})")
                result = await process_config(config, uploader)
                logger.info(f"[parallel] finished config #{config.id}: new={result.get('new', 0)}")
                return result

        results = await asyncio.gather(
            *[_run_with_sem(cfg) for cfg in configs],
            return_exceptions=True,
        )
        for cfg, r in zip(configs, results):
            if isinstance(r, Exception):
                logger.error(f"[parallel] config #{cfg.id} raised: {r}")
                total["errors"] = total.get("errors", 0) + 1
            else:
                for k, v in r.items():
                    total[k] = total.get(k, 0) + v
    else:
        for i, config in enumerate(configs, 1):
            logger.info(f"--- [{i}/{len(configs)}] config #{config.id} ---")
            stats = await process_config(config, uploader)
            for k, v in stats.items():
                total[k] = total.get(k, 0) + v

            if i < len(configs):
                logger.info("Rotating IP before next config")
                await rotate_ip()

    logger.info(f"=== WORKER FINISHED. Totals: {total} ===")
    return total


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run parser worker")
    parser.add_argument("limit", nargs="?", type=int, default=None,
                        help="Max number of active configs to process (positional)")
    parser.add_argument("--config-id", type=int, default=None,
                        help="Run a single config by ID (ignores is_active)")
    args = parser.parse_args()
    asyncio.run(run_once(limit=args.limit, config_id=args.config_id))
