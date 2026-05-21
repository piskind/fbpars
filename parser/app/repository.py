from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Ad, Creative, ModerationEntry, AdMediaType, ModerationStatus
from app.parsers.library_card import ParsedCard


async def find_ad_by_library_id(session: AsyncSession, library_id: str) -> Ad | None:
    stmt = select(Ad).where(Ad.library_id == library_id)
    return (await session.execute(stmt)).scalar_one_or_none()


def _detect_media_type(card: ParsedCard) -> AdMediaType:
    if card.video_urls:
        return AdMediaType.VIDEO
    if len(card.image_urls) > 1:
        return AdMediaType.CAROUSEL
    if card.image_urls:
        return AdMediaType.IMAGE
    return AdMediaType.UNKNOWN


def _extract_page_id(page_url: str | None) -> str | None:
    if not page_url:
        return None
    import re
    m = re.search(r"facebook\.com/(\d+)", page_url)
    return m.group(1) if m else None


async def upsert_ad(
    session: AsyncSession,
    card: ParsedCard,
    country: str,
    keyword: str,
    vertical: str = "nutra",
) -> tuple[Ad, bool]:
    now = datetime.now(timezone.utc)
    existing = await find_ad_by_library_id(session, card.library_id)
    if existing:
        existing.is_active = card.is_active
        existing.last_seen_at = now
        existing.last_refresh_at = now
        if card.started_at and not existing.started_at:
            existing.started_at = card.started_at
        if not existing.vertical:
            existing.vertical = vertical
        await session.flush()
        return existing, False
    ad = Ad(
        library_id=card.library_id,
        country=country,
        keyword=keyword,
        vertical=vertical,
        page_id=_extract_page_id(card.page_url),
        page_name=card.page_name,
        page_url=card.page_url,
        title=None,
        body=card.body_text,
        cta_text=card.cta_text,
        link_url=card.link_url,
        display_url=card.display_url,
        media_type=_detect_media_type(card),
        started_at=card.started_at,
        is_active=card.is_active,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(ad)
    await session.flush()
    moderation = ModerationEntry(ad_id=ad.id, status=ModerationStatus.PENDING)
    session.add(moderation)
    await session.flush()
    return ad, True

async def save_creative(
    session: AsyncSession,
    ad_id: int,
    media_type: AdMediaType,
    upload_result: dict,
) -> Creative:
    creative = Creative(
        ad_id=ad_id,
        media_type=media_type,
        original_url=upload_result["original_url"],
        s3_key=upload_result["s3_key"],
        s3_url=upload_result["s3_url"],
        md5=upload_result["md5"],
        phash=upload_result["phash"],
        width=upload_result["width"],
        height=upload_result["height"],
        file_size=upload_result["file_size"],
        downloaded=True,
    )
    session.add(creative)
    await session.flush()
    return creative