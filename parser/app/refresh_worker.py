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

# JS: find the external landing URL on the ?id= page.
# Works for both active (CTA button present) and inactive (URL may be in data attrs).
_URL_SCRIPT = """
() => {
    const decode = (href) => {
        try {
            if (href.includes('l.facebook.com') || href.includes('l.fb.me')) {
                const dest = new URL(href).searchParams.get('u');
                if (dest) return decodeURIComponent(dest);
            }
        } catch(e) {}
        return null;
    };
    for (const a of document.querySelectorAll('a[href]')) {
        const href = a.href || '';
        if (!href || href.startsWith('about:') || href.includes('facebook.com/ads/library')) continue;
        const dec = decode(href);
        if (dec && !dec.includes('facebook.com')) return dec;
        if (!href.includes('facebook.com')) return href;
    }
    for (const node of document.querySelectorAll('[data-lynx-uri]')) {
        const uri = node.getAttribute('data-lynx-uri') || '';
        if (uri && !uri.includes('facebook.com')) return uri;
    }
    for (const node of document.querySelectorAll('[data-store]')) {
        try {
            const s = JSON.parse(node.getAttribute('data-store') || '{}');
            if (s.url && !s.url.includes('facebook.com')) return s.url;
        } catch(e) {}
    }
    return null;
}
"""


def _build_ad_url(library_id: str) -> str:
    return f"https://www.facebook.com/ads/library/?id={library_id}"


async def _load_card_text(library_id: str) -> tuple[str | bool | None, str | None]:
    """
    Opens a fresh browser context, loads the ?id= page.
    Returns (card_result, found_url) where:
      card_result: str = card innerText | False = page OK but no card | None = couldn't verify
      found_url:   str = external landing URL found on page | None = not found
    One visit does both: status check AND link enrichment.
    """
    url = _build_ad_url(library_id)
    async with browser_context() as context:
        page = await context.new_page()
        try:
            ok = await goto_with_challenge_retry(page, url, max_attempts=4, base_wait=6)
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: network error {e}")
            return None, None

        if not ok:
            logger.warning(f"[refresh] {library_id}: challenge not cleared after retries → skip")
            return None, None

        await asyncio.sleep(2)

        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        _card_script = f"""
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
            card_result = await asyncio.wait_for(page.evaluate(_card_script), timeout=20)
        except asyncio.TimeoutError:
            logger.warning(f"[refresh] {library_id}: evaluate timeout → skip")
            return None, None
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: evaluate error {e}")
            return None, None

        # Extract landing URL in the same page visit
        found_url: str | None = None
        try:
            found_url = await asyncio.wait_for(page.evaluate(_URL_SCRIPT), timeout=10)
        except Exception:
            pass

        if card_result.get("text"):
            return card_result["text"], found_url

        if card_result.get("bodyLen", 9999) < 500:
            logger.warning(f"[refresh] {library_id}: page nearly empty → skip")
            return None, None

        return False, found_url


async def _check_ad_on_fb(library_id: str) -> tuple[bool | None, str | None]:
    """
    Returns (is_active, found_url):
      is_active: True = active | False = genuinely gone | None = couldn't verify
      found_url: external landing URL extracted from the page, or None
    """
    card_text, found_url = await _load_card_text(library_id)

    if card_text is None:
        return None, None

    if card_text is not False:
        try:
            card = parse_card_text(card_text)
            return bool(card.is_active), found_url
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: parse error {e}")
            return None, found_url

    # Card not found on first attempt — retry once to rule out transient proxy disruption
    logger.warning(f"[refresh] {library_id}: card not found on first attempt, retrying in 15s")
    await asyncio.sleep(15)

    card_text2, found_url2 = await _load_card_text(library_id)

    if card_text2 is None:
        return None, None

    if card_text2 is not False:
        try:
            card = parse_card_text(card_text2)
            return bool(card.is_active), found_url2 or found_url
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: parse error on retry {e}")
            return None, found_url2 or found_url

    logger.info(f"[refresh] {library_id}: card not found after retry → INACTIVE")
    return False, found_url2 or found_url


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
    stats = {"checked": 0, "deactivated": 0, "still_active": 0, "unknown": 0, "errors": 0, "url_enriched": 0}

    async with AsyncSessionLocal() as session:
        ads = await _get_active_ads(session, limit)

    logger.info(f"[refresh] Starting batch of {len(ads)} ads")

    for i, ad in enumerate(ads, 1):
        library_id = ad.library_id
        logger.info(f"[refresh] [{i}/{len(ads)}] checking {library_id}")

        try:
            is_active, found_url = await _check_ad_on_fb(library_id)
        except Exception as e:
            logger.error(f"[refresh] {library_id}: unexpected error {e}")
            is_active, found_url = None, None
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

            # Enrich link_url if missing and we found one on the page
            if found_url and not db_ad.link_url:
                db_ad.link_url = found_url
                stats["url_enriched"] += 1
                logger.info(f"[refresh] {library_id}: link_url enriched → {found_url}")

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
