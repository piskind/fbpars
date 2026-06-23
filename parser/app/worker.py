import asyncio
from datetime import datetime, timezone
from sqlalchemy import select, func
from app.models import Ad, Creative
from loguru import logger
from app.db import AsyncSessionLocal
from app.models import ParsingConfig, AdMediaType
from app.browser import browser_context, build_library_url
from app.parsers.library_card import parse_card_text
from app.parsers.library_extractor import scroll_and_collect
from app.repository import upsert_ad, save_creative
from app.storage import MediaUploader
from app.proxy import rotate_ip, current_ip

# Shared across all concurrent configs if run_once ever parallelises process_config calls.
# Currently one config runs at a time, so this is effectively per-config.
MEDIA_SEMAPHORE = asyncio.Semaphore(10)


async def get_active_configs(session) -> list[ParsingConfig]:
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

        for idx, vid_url in enumerate(card.video_urls[:2]):
            upload = await uploader.upload_video(card.library_id, vid_url, idx)
            if upload:
                vid_uploads.append(upload)
            else:
                fail_count += 1

        if not card.image_urls and not card.video_urls:
            for idx, poster_url in enumerate(card.poster_urls[:2]):
                upload = await uploader.upload_image(card.library_id, poster_url, idx)
                if upload:
                    img_uploads.append(upload)
                else:
                    fail_count += 1

    s["media_fail"] = fail_count
    media_saved = len(img_uploads) + len(vid_uploads)
    s["media_ok"] = media_saved

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
            db_ad = await session.get(Ad, ad_id)
            if db_ad:
                await session.delete(db_ad)
            await session.commit()
            s["media_ok"] -= media_saved
            s["skipped_phash_duplicate"] += 1
            logger.info(f"[#{config_id}] skipped {card.library_id}: visual duplicate (phash)")
            return s

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


async def process_config(config: ParsingConfig, uploader: MediaUploader) -> dict:
    # For auto_date_from_last_parse: use last_parsed_at as date_from if set
    effective_date_from = config.date_from
    if config.auto_date_from_last_parse and config.last_parsed_at:
        effective_date_from = config.last_parsed_at.date()

    url = build_library_url(
        config.country,
        config.keyword,
        config.languages,
        active_status=config.active_status or "all",
        media_type=config.media_type_filter or "all",
        platforms=config.platforms,
        date_from=effective_date_from,
        date_to=config.date_to,
        advertiser=config.advertiser,
    )
    kw_tag = config.keyword or "(no keyword)"
    lang_tag = f" lang={config.languages}" if config.languages else ""
    type_tag = f" [{config.config_type}]"
    logger.info(f"[#{config.id}]{type_tag} {kw_tag}/{config.country}{lang_tag} → {url}")

    stats = {
        "raw": 0,
        "new": 0,
        "updated": 0,
        "media_ok": 0,
        "media_fail": 0,
        "errors": 0,
        "skipped_duplicate": 0,
        "skipped_no_media": 0,
        "skipped_already_rejected": 0,
        "skipped_phash_duplicate": 0,
        "removed_no_media": 0,
    }

    try:
        async with browser_context() as context:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            try:
                await page.wait_for_selector('div:has-text("Library ID")', timeout=20_000)
            except Exception:
                logger.warning(f"[#{config.id}] No 'Library ID' on page, probably empty results")
                return stats

            await asyncio.sleep(5)
            # filters/fanpage configs browse broadly — cap scrolls lower; MAX_CARDS=300 is the hard limit
            max_scrolls = 40 if config.config_type in ("filters", "fanpage") else 80
            raw_cards = await asyncio.wait_for(
                scroll_and_collect(page, max_scrolls=max_scrolls, stable_rounds=7),
                timeout=600,  # 10 min hard ceiling — prevents hang when browser is OOM-killed
            )
            stats["raw"] = len(raw_cards)

        # Phase 1: sequential DB upserts (browser already closed)
        seen_ids: set[str] = set()
        media_tasks: list[tuple[int, object]] = []

        for raw in raw_cards:
            card = parse_card_text(raw["text"])
            if not card.library_id:
                continue
            if card.library_id in seen_ids:
                stats["skipped_duplicate"] += 1
                continue
            seen_ids.add(card.library_id)
            card.image_urls = [img["src"] for img in raw["images"]]
            card.video_urls = [v["src"] for v in raw["videos"] if v["src"]]
            card.poster_urls = [v["poster"] for v in raw["videos"] if v["poster"]]
            card.page_url = raw["page_url"]
            card.link_url = raw["external_url"]

            if not card.image_urls and not card.video_urls and not card.poster_urls:
                logger.info(f"[#{config.id}] skip {card.library_id}: no media in card")
                stats["skipped_no_media"] += 1
                continue

            async with AsyncSessionLocal() as session:
                try:
                    ad, is_new, skipped = await upsert_ad(
                        session, card, config.country, config.keyword, config.vertical
                    )
                    await session.commit()
                    if is_new:
                        stats["new"] += 1
                    elif skipped:
                        stats["skipped_already_rejected"] += 1
                    else:
                        stats["updated"] += 1
                except Exception as e:
                    logger.warning(f"[#{config.id}] DB error for {card.library_id}: {e}")
                    await session.rollback()
                    stats["errors"] += 1
                    continue

            if is_new and not skipped:
                media_tasks.append((ad.id, card))

        # Phase 2: parallel media downloads, semaphore limits to 10 concurrent
        logger.info(f"[#{config.id}] starting parallel media for {len(media_tasks)} new ads")
        results = await asyncio.gather(
            *[_upload_card_media(uploader, config.id, ad_id, card)
              for ad_id, card in media_tasks],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"[#{config.id}] media task error: {r}")
                stats["errors"] += 1
            else:
                for k, v in r.items():
                    stats[k] = stats.get(k, 0) + v

    except Exception as e:
        logger.error(f"[#{config.id}] Fatal error: {e}")
        stats["errors"] += 1

    # Always stamp last_parsed_at so auto_date_from_last_parse advances on next run
    async with AsyncSessionLocal() as session:
        cfg = await session.get(ParsingConfig, config.id)
        if cfg:
            cfg.last_parsed_at = datetime.now(timezone.utc)
            await session.commit()

    logger.info(f"[#{config.id}] DONE: {stats}")
    return stats


async def run_once(limit: int | None = None) -> None:
    ip = await current_ip()
    logger.info(f"Starting worker. Current IP: {ip}")

    async with AsyncSessionLocal() as session:
        configs = await get_active_configs(session)

    if limit:
        configs = configs[:limit]
    logger.info(f"Loaded {len(configs)} active configs")

    uploader = MediaUploader()
    total = {
        "raw": 0, "new": 0, "updated": 0, "media_ok": 0, "media_fail": 0, "errors": 0,
        "skipped_duplicate": 0, "skipped_no_media": 0, "skipped_already_rejected": 0,
        "skipped_phash_duplicate": 0, "removed_no_media": 0,
    }

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
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(run_once(limit))
