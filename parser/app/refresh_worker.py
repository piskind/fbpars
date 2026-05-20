"""
Refresh worker: обходит активные объявления из БД, проверяет их статус на FB,
обновляет is_active, last_seen_at, days_active.
"""
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select, update
from loguru import logger

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Ad
from app.browser import browser_context
from app.parsers.library_card import parse_card_text
from app.proxy import rotate_ip


REFRESH_INTERVAL_HOURS = 6
BATCH_SIZE = 50  # объявлений за один проход воркера


def _build_ad_url(library_id: str) -> str:
    return f"https://www.facebook.com/ads/library/?id={library_id}"


async def _check_ad_on_fb(library_id: str) -> bool | None:
    """
    Возвращает True если объявление активно, False если неактивно,
    None если страница не загрузилась или объявление не найдено.
    """
    url = _build_ad_url(library_id)
    try:
        async with browser_context() as context:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            try:
                await page.wait_for_selector('div:has-text("Library ID:")', timeout=15_000)
            except Exception:
                logger.warning(f"[refresh] {library_id}: no card found on page")
                return None

            await asyncio.sleep(2)
            text = await page.inner_text("body")

            head = text[:500]
            if "Inactive" in head:
                return False
            if "Active" in head:
                return True

            card_text = ""
            divs = await page.query_selector_all("div")
            for div in divs:
                t = await div.inner_text()
                if f"Library ID: {library_id}" in t and len(t) < 5000:
                    card_text = t
                    break

            if card_text:
                card = parse_card_text(card_text)
                return card.is_active

            return None
    except Exception as e:
        logger.error(f"[refresh] {library_id}: error {e}")
        return None


async def _get_active_ads(session, limit: int) -> list[Ad]:
    stmt = (
        select(Ad)
        .where(Ad.is_active.is_(True))
        .order_by(Ad.last_refresh_at.nulls_first(), Ad.id)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def refresh_batch(limit: int = BATCH_SIZE) -> dict:
    now = datetime.now(timezone.utc)
    stats = {"checked": 0, "deactivated": 0, "still_active": 0, "unknown": 0, "errors": 0}

    async with AsyncSessionLocal() as session:
        ads = await _get_active_ads(session, limit)

    logger.info(f"[refresh] Starting batch of {len(ads)} ads")

    for i, ad in enumerate(ads, 1):
        library_id = ad.library_id
        logger.info(f"[refresh] [{i}/{len(ads)}] checking {library_id}")

        is_active = await _check_ad_on_fb(library_id)
        stats["checked"] += 1

        async with AsyncSessionLocal() as session:
            db_ad = await session.get(Ad, ad.id)
            if not db_ad:
                continue

            db_ad.last_refresh_at = now

            if is_active is None:
                stats["unknown"] += 1
            elif is_active:
                db_ad.last_seen_at = now
                db_ad.days_active = _compute_days_active(db_ad, now)
                stats["still_active"] += 1
            else:
                db_ad.is_active = False
                db_ad.days_active = _compute_days_active(db_ad, now)
                stats["deactivated"] += 1
                logger.info(f"[refresh] {library_id}: marked INACTIVE")

            await session.commit()

        if i < len(ads) and i % 10 == 0:
            logger.info("[refresh] Rotating IP")
            await rotate_ip()

    logger.info(f"[refresh] Batch done: {stats}")
    return stats


def _compute_days_active(ad: Ad, now: datetime) -> int:
    ref = ad.started_at or ad.first_seen_at
    if ref:
        ref = ref.replace(tzinfo=timezone.utc) if ref.tzinfo is None else ref
        return max(0, (now - ref).days)
    return ad.days_active


async def run_refresh_loop():
    logger.info(f"[refresh] Loop started, interval={REFRESH_INTERVAL_HOURS}h, batch={BATCH_SIZE}")
    while True:
        try:
            await refresh_batch()
        except Exception as e:
            logger.error(f"[refresh] Loop error: {e}")
        logger.info(f"[refresh] Sleeping {REFRESH_INTERVAL_HOURS}h until next run")
        await asyncio.sleep(REFRESH_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else BATCH_SIZE
    asyncio.run(refresh_batch(limit))
