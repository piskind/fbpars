import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from loguru import logger

from app.db import AsyncSessionLocal
from app.models import Ad
from app.browser import browser_context, goto_with_challenge_retry
from app.parsers.library_card import parse_card_text
from app.proxy import rotate_ip


REFRESH_INTERVAL_HOURS = 6
BATCH_SIZE = 50


def _build_ad_url(library_id: str) -> str:
    return f"https://www.facebook.com/ads/library/?id={library_id}"


async def _load_card_text(library_id: str) -> bool | None | str:
    """
    Opens a fresh browser context, loads the ?id= page, and returns:
      str   — card innerText found
      False — page loaded OK but card genuinely absent
      None  — couldn't verify (challenge exhausted, network error, empty page)
    """
    url = _build_ad_url(library_id)
    async with browser_context() as context:
        page = await context.new_page()
        try:
            ok = await goto_with_challenge_retry(page, url, max_attempts=4, base_wait=6)
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: network error {e}")
            return None

        if not ok:
            logger.warning(f"[refresh] {library_id}: challenge not cleared after retries → skip")
            return None

        await asyncio.sleep(2)

        # FB SPA may do another navigation after challenge reload — wait for it to settle
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        _script = f"""
            () => {{
                const needle = 'Library ID: {library_id}';
                for (const el of document.querySelectorAll('div')) {{
                    const t = el.innerText || '';
                    if (t.includes(needle) && t.length > 100 && t.length < 10000) {{
                        return {{ text: t, bodyLen: document.body.innerText.length }};
                    }}
                }}
                return {{ text: null, bodyLen: document.body.innerText.length }};
            }}
        """
        try:
            result = await asyncio.wait_for(page.evaluate(_script), timeout=20)
        except asyncio.TimeoutError:
            logger.warning(f"[refresh] {library_id}: evaluate timeout → skip")
            return None
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: evaluate error {e}")
            return None

        if result.get("text"):
            return result["text"]

        if result.get("bodyLen", 9999) < 500:
            logger.warning(f"[refresh] {library_id}: page nearly empty → skip")
            return None

        return False


async def _check_ad_on_fb(library_id: str) -> bool | None:
    """
    Returns:
      True  — ad is active on FB
      False — ad is genuinely gone (card absent on a properly-loaded page, confirmed by retry)
      None  — couldn't verify (challenge, network, proxy disruption) — do not change status

    Per-ad browser_context ensures fresh session; _BROWSER_SEMAPHORE prevents OOM.
    """
    result = await _load_card_text(library_id)

    if result is None:
        return None

    if result is not False:
        # Got card text — parse active status
        try:
            card = parse_card_text(result)
            return bool(card.is_active)
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: parse error {e}")
            return None

    # Card not found on first attempt — retry once after a delay to rule out
    # transient proxy disruption (e.g. discovery worker just rotated IP).
    logger.warning(f"[refresh] {library_id}: card not found on first attempt, retrying in 15s")
    await asyncio.sleep(15)

    result2 = await _load_card_text(library_id)

    if result2 is None:
        return None

    if result2 is not False:
        try:
            card = parse_card_text(result2)
            return bool(card.is_active)
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: parse error on retry {e}")
            return None

    logger.info(f"[refresh] {library_id}: card not found after retry → INACTIVE")
    return False


async def _get_active_ads(session, limit: int) -> list[Ad]:
    stmt = (
        select(Ad)
        .where(Ad.is_active.is_(True))
        .order_by(Ad.last_refresh_at.nulls_first(), Ad.id)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


def _compute_days_active(ad: Ad, now: datetime) -> int:
    ref = ad.started_at or ad.first_seen_at
    if ref:
        ref = ref.replace(tzinfo=timezone.utc) if ref.tzinfo is None else ref
        return max(0, (now - ref).days)
    return ad.days_active


async def refresh_batch(limit: int = BATCH_SIZE) -> dict:
    now = datetime.now(timezone.utc)
    stats = {"checked": 0, "deactivated": 0, "still_active": 0, "unknown": 0, "errors": 0}

    async with AsyncSessionLocal() as session:
        ads = await _get_active_ads(session, limit)

    logger.info(f"[refresh] Starting batch of {len(ads)} ads")

    for i, ad in enumerate(ads, 1):
        library_id = ad.library_id
        logger.info(f"[refresh] [{i}/{len(ads)}] checking {library_id}")

        try:
            is_active = await _check_ad_on_fb(library_id)
        except Exception as e:
            logger.error(f"[refresh] {library_id}: unexpected error {e}")
            is_active = None
            stats["errors"] += 1
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

        if i < len(ads) and i % 5 == 0:
            logger.info("[refresh] Rotating IP")
            await rotate_ip()

    logger.info(f"[refresh] Batch done: {stats}")
    return stats


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
