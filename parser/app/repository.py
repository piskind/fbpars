from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger
from app.models import Ad, Creative, ModerationEntry, AdMediaType, ModerationStatus
from app.parsers.library_card import ParsedCard
from app.enrich import enrich_ad_fields


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
    keyword: str | None,
    vertical: str = "nutra",
    config_type: str = "keyword",
) -> tuple[Ad, bool, bool]:
    """Returns (ad, is_new, skipped_moderated).

    skipped_moderated=True means the ad already has REJECTED/APPROVED status —
    caller should count it separately and skip media upload.
    """
    now = datetime.now(timezone.utc)

    enriched = await enrich_ad_fields(card.link_url, card.body_text)
    page_id = _extract_page_id(card.page_url)

    existing = await find_ad_by_library_id(session, card.library_id)
    if existing:
        # Check moderation status — don't re-create moderation entry for
        # already reviewed ads, and skip expensive field updates for rejected ones.
        mod_entry = (await session.execute(
            select(ModerationEntry).where(ModerationEntry.ad_id == existing.id)
        )).scalar_one_or_none()

        is_reviewed = mod_entry and mod_entry.status in (ModerationStatus.REJECTED, ModerationStatus.APPROVED)

        existing.is_active = card.is_active
        existing.last_seen_at = now
        existing.last_refresh_at = now
        if card.started_at and not existing.started_at:
            existing.started_at = card.started_at
        ref = existing.started_at or existing.first_seen_at
        if ref:
            ref = ref.replace(tzinfo=timezone.utc) if ref.tzinfo is None else ref
            existing.days_active = max(0, (now - ref).days)
        if not existing.vertical:
            existing.vertical = vertical

        if card.title and not existing.title:
            existing.title = card.title
        if card.body_text and not existing.body:
            existing.body = card.body_text
        if card.caption and not existing.caption:
            existing.caption = card.caption
        if card.platforms and not existing.platforms:
            existing.platforms = card.platforms
        if card.lead_form and not existing.lead_form:
            existing.lead_form = True
        existing.used_in_ads_count = card.used_in_ads_count
        if card.page_name and not existing.page_name:
            existing.page_name = card.page_name
        if page_id and not existing.page_id:
            existing.page_id = page_id
        if card.page_url and not existing.page_url:
            existing.page_url = card.page_url
        if card.link_url and not existing.link_url:
            existing.link_url = card.link_url
        if card.display_url and not existing.display_url:
            existing.display_url = card.display_url
        if card.cta_text and not existing.cta_text:
            existing.cta_text = card.cta_text

        if enriched["app_store"] and not existing.app_store:
            existing.app_store = enriched["app_store"]
        if enriched["ecom_platform"] and not existing.ecom_platform:
            existing.ecom_platform = enriched["ecom_platform"]
        if enriched["language"] and not existing.language:
            existing.language = enriched["language"]
        if enriched["ip"] and not existing.ip:
            existing.ip = enriched["ip"]

        await session.flush()
        logger.debug(f"updated {card.library_id}: reviewed={is_reviewed}")
        return existing, False, is_reviewed

    ad = Ad(
        library_id=card.library_id,
        country=country,
        keyword=keyword,
        vertical=vertical,
        page_id=page_id,
        page_name=card.page_name,
        page_url=card.page_url,
        title=card.title,
        body=card.body_text,
        caption=card.caption,
        cta_text=card.cta_text,
        link_url=card.link_url,
        display_url=card.display_url,
        media_type=_detect_media_type(card),
        platforms=card.platforms or None,
        lead_form=card.lead_form,
        used_in_ads_count=card.used_in_ads_count,
        app_store=enriched["app_store"],
        ecom_platform=enriched["ecom_platform"],
        language=enriched["language"],
        ip=enriched["ip"],
        started_at=card.started_at,
        is_active=card.is_active,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(ad)
    await session.flush()
    mod_status = ModerationStatus.APPROVED if config_type in ("filters", "fanpage") else ModerationStatus.PENDING
    moderation = ModerationEntry(ad_id=ad.id, status=mod_status)
    session.add(moderation)
    await session.flush()
    return ad, True, False


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
