import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from app.models import Ad
from loguru import logger
from app.db import AsyncSessionLocal
from app.models import ParsingConfig, AdMediaType
from app.browser import browser_context, build_library_url
from app.parsers.library_card import parse_card_text
from app.parsers.library_extractor import scroll_and_collect
from app.repository import upsert_ad, save_creative
from app.storage import MediaUploader
from app.proxy import rotate_ip, current_ip


async def get_active_configs(session) -> list[ParsingConfig]:
    stmt = select(ParsingConfig).where(ParsingConfig.is_active.is_(True)).order_by(ParsingConfig.id)
    return list((await session.execute(stmt)).scalars().all())


async def process_config(config: ParsingConfig, uploader: MediaUploader) -> dict:
    url = build_library_url(config.country, config.keyword)
    logger.info(f"[#{config.id}] {config.keyword}/{config.country} → {url}")

    stats = {"raw": 0, "new": 0, "updated": 0, "media_ok": 0, "media_fail": 0, "errors": 0}

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
            raw_cards = await scroll_and_collect(page, max_scrolls=30)
            stats["raw"] = len(raw_cards)

        seen_ids: set[str] = set()
        for raw in raw_cards:
            card = parse_card_text(raw["text"])
            if not card.library_id:
                continue
            if card.library_id in seen_ids:
                stats["skipped_duplicate"] = stats.get("skipped_duplicate", 0) + 1
                continue
            seen_ids.add(card.library_id)
            card.image_urls = [img["src"] for img in raw["images"]]
            card.video_urls = [v["src"] for v in raw["videos"] if v["src"]]
            card.poster_urls = [v["poster"] for v in raw["videos"] if v["poster"]]
            card.page_url = raw["page_url"]
            card.link_url = raw["external_url"]

            if not card.image_urls and not card.video_urls:
                logger.info(f"[#{config.id}] skip {card.library_id}: no media in card")
                stats["skipped_no_media"] = stats.get("skipped_no_media", 0) + 1
                continue

            async with AsyncSessionLocal() as session:
                try:
                    ad, is_new, skipped = await upsert_ad(session, card, config.country, config.keyword, config.vertical)
                    await session.commit()
                    if is_new:
                        stats["new"] += 1
                    elif skipped:
                        stats["skipped_already_rejected"] = stats.get("skipped_already_rejected", 0) + 1
                    else:
                        stats["updated"] += 1
                except Exception as e:
                    logger.warning(f"[#{config.id}] DB error for {card.library_id}: {e}")
                    await session.rollback()
                    stats["errors"] += 1
                    continue

                if not is_new or skipped:
                    continue

                ad_id = ad.id

            for idx, img_url in enumerate(card.image_urls[:3]):
                upload = await uploader.upload_image(card.library_id, img_url, idx)
                if upload:
                    async with AsyncSessionLocal() as session:
                        await save_creative(session, ad_id, AdMediaType.IMAGE, upload)
                        await session.commit()
                    stats["media_ok"] += 1
                else:
                    stats["media_fail"] += 1

            for idx, vid_url in enumerate(card.video_urls[:2]):
                upload = await uploader.upload_video(card.library_id, vid_url, idx)
                if upload:
                    async with AsyncSessionLocal() as session:
                        await save_creative(session, ad_id, AdMediaType.VIDEO, upload)
                        await session.commit()
                    stats["media_ok"] += 1
                else:
                    stats["media_fail"] += 1

        async with AsyncSessionLocal() as session:
                from sqlalchemy import select, func
                from app.models import Creative
                cnt = await session.scalar(
                    select(func.count(Creative.id)).where(
                        Creative.ad_id == ad_id, Creative.s3_url.is_not(None)
                    )
                )
                if not cnt:
                    db_ad = await session.get(Ad, ad_id)
                    if db_ad:
                        await session.delete(db_ad)
                        await session.commit()
                        logger.info(f"[#{config.id}] removed {card.library_id}: no media saved")
                        stats["removed_no_media"] = stats.get("removed_no_media", 0) + 1

    except Exception as e:
        logger.error(f"[#{config.id}] Fatal error: {e}")
        stats["errors"] += 1

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
    total = {"raw": 0, "new": 0, "updated": 0, "media_ok": 0, "media_fail": 0, "errors": 0, "skipped_no_media": 0}

    for i, config in enumerate(configs, 1):
        logger.info(f"--- [{i}/{len(configs)}] config #{config.id} ---")
        stats = await process_config(config, uploader)
        for k, v in stats.items():
            total[k] = total.get(k, 0) + v

        if i < len(configs):
            logger.info("Rotating IP before next config")
            await rotate_ip()

    logger.info(f"=== WORKER FINISHED. Totals: {total} ===")


if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(run_once(limit))