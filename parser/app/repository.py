from datetime import datetime, timezone
import hashlib

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger
from app.models import Ad, Creative, ModerationEntry, AdMediaType, ModerationStatus
from app.parsers.library_card import ParsedCard
from app.enrich import enrich_ad_fields


async def find_ad_by_library_id_and_country(
    session: AsyncSession, library_id: str, country: str
) -> Ad | None:
    """Поиск объявления БЕЗ привязки к стране — одно объявление = одна строка.

    Раньше уникальность была составной (library_id + страна сбора), и одно и то же
    объявление, найденное по нескольким гео, сохранялось несколько раз: так набралось
    1.29 млн повторов из 7.26 млн строк. Теперь ищем по library_id: повторная находка
    обновляет существующую строку (дописывает недостающие поля и дату показа), а не
    плодит копию. Страна у объявления остаётся та, под которой его нашли первым.

    Замечание: гео в поиске FB всё равно не фильтрует, поэтому country у нас — метка
    сбора, а не таргетинг объявления, и терять на этом нечего.
    """
    stmt = select(Ad).where(Ad.library_id == library_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _est_takoy_zhe_kreativ(session: AsyncSession, card: ParsedCard,
                                 page_id: str | None) -> bool:
    """Уже есть объявление с ТЕМ ЖЕ креативом от той же страницы?

    Facebook разрешает крутить одну картинку с одним текстом десятками отдельных
    объявлений: у них разные library_id, но креатив один. Замер на Мексике за 7 июля:
    из ~18 400 объявлений уникальных креативов всего 4 375, то есть 87% — повторы.
    Отпечаток берём по (страница + текст + заголовок), под него есть индекс.
    """
    telo = (card.body_text or "").strip()
    zagolovok = (card.title or "").strip()
    # Если содержимого нет, отпечаток вырождается в md5 пустой строки и совпадает
    # у ВСЕХ таких объявлений (их в базе 92 тысячи). Тогда проверка отбрасывала бы
    # каждую новую карточку без текста как повтор — сбор почти вставал. Не проверяем.
    if not telo and not zagolovok:
        return False
    # ВРЕМЕННО ОТКЛЮЧЕНО: индекс по отпечатку оказался построен по другому
    # выражению, из-за чего этот запрос делал Seq Scan по 6 млн строк НА КАЖДУЮ
    # карточку — нагрузка на базу 12, сбор почти встал. Повторы внутри пачки
    # продолжают отсекаться в worker (batch_fp), этого достаточно.
    return False
    otpechatok = hashlib.md5(
        ((page_id or "") + telo + zagolovok).encode("utf-8")
    ).hexdigest()
    stmt = text(
        "SELECT 1 FROM ads WHERE md5(coalesce(page_id,'') || coalesce(body,'')"
        " || coalesce(title,'')) = :fp LIMIT 1"
    )
    return (await session.execute(stmt, {"fp": otpechatok})).first() is not None


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
    page_id = getattr(card, "page_id", None) or _extract_page_id(card.page_url)

    existing = await find_ad_by_library_id_and_country(session, card.library_id, country)

    # Новое объявление, но креатив уже есть у этой страницы — не сохраняем.
    # Это ровно те «дубли», которые видит заказчик: один и тот же баннер,
    # запущенный рекламодателем десятки раз отдельными объявлениями.
    if existing is None and await _est_takoy_zhe_kreativ(session, card, page_id):
        return None, False, True

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
        # ended_at обновляем всегда: объявление могло остановиться между сборами
        if card.ended_at:
            existing.ended_at = card.ended_at
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

        # Refresh direct FB CDN media URLs on every re-parse — they carry short-lived
        # signatures, so overwrite (rather than fill-if-empty) to keep them loadable.
        if card.image_urls:
            existing.image_urls = card.image_urls
        if card.video_urls:
            existing.video_urls = card.video_urls
        if card.poster_urls:
            existing.poster_urls = card.poster_urls

        if enriched["app_store"] and not existing.app_store:
            existing.app_store = enriched["app_store"]
        if enriched["ecom_platform"] and not existing.ecom_platform:
            existing.ecom_platform = enriched["ecom_platform"]
        if enriched["language"] and not existing.language:
            existing.language = enriched["language"]
        if enriched["ip"] and not existing.ip:
            existing.ip = enriched["ip"]

        await session.flush()
        # TRACE, not DEBUG: this fires once per card (hundreds/sec during a run) and used to
        # bury the useful lines. Still available at LOG_LEVEL=TRACE for deep debugging.
        logger.trace(f"updated {card.library_id}: reviewed={is_reviewed}")
        return existing, False, is_reviewed

    ad = Ad(
        library_id=card.library_id,
        country=country,
        ended_at=card.ended_at,
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
        image_urls=card.image_urls or None,
        video_urls=card.video_urls or None,
        poster_urls=card.poster_urls or None,
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
    # Все типы сборов идут в витрину сразу. Раньше keyword уходил в PENDING, и
    # собранное по ключам до заказчика не доезжало вовсе — 6 090 карточек висели
    # непросмотренными с июля.
    mod_status = ModerationStatus.APPROVED
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
