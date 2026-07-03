from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, and_, distinct, text, String, tuple_, cast
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import get_current_client
from app.models_proxy import (
    Ad,
    Creative,
    ModerationEntry,
    ModerationStatus,
    ClientUser,
    ParsingConfig,
)
from app.schemas import AdOut


router = APIRouter(prefix="/api/feed", tags=["feed"])


def _host_from_url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip()
    if "://" in u:
        u = u.split("://", 1)[1]
    u = u.split("/", 1)[0].split("?", 1)[0]
    return u or None


def _normalize_domain(d: str | None) -> str | None:
    if not d:
        return None
    d = d.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    if "/" in d:
        d = d.split("/", 1)[0]
    return d or None


# Все фильтры /feed в одном месте — используются и списком, и счётчиком.
def _apply_ad_filters(
    stmt,
    *,
    country=None,
    countries=None,
    keyword=None,
    vertical=None,
    media_type=None,
    cta=None,
    platforms=None,
    domain=None,
    page_id=None,
    page_name=None,
    link_contains=None,
    language=None,
    lead_form=None,
    app_store=None,
    ecom_platform=None,
    ip=None,
    search=None,
    search_mode="exact",
    partner=None,
    country_count=None,
    is_active=None,
    days_active_min=None,
    days_active_max=None,
    started_from=None,
    started_to=None,
    last_seen_from=None,
    last_seen_to=None,
    reach_min=None,
    spend_min=None,
    eu_country=None,
    used_in_ads_min=None,
):
    if countries:
        stmt = stmt.where(Ad.country.in_(countries))
    elif country:
        stmt = stmt.where(Ad.country == country)
    if keyword:
        stmt = stmt.where(Ad.keyword == keyword)
    if vertical:
        stmt = stmt.where(Ad.vertical == vertical)
    if media_type:
        stmt = stmt.where(Ad.media_type == media_type)
    if cta:
        stmt = stmt.where(Ad.cta_text.ilike(f"%{cta}%"))
    if platforms:
        stmt = stmt.where(Ad.platforms.op("&&")(platforms))  # array overlap
    if domain:
        nd = _normalize_domain(domain)
        if nd:
            stmt = stmt.where(or_(
                Ad.display_url.ilike(f"%{nd}%"),
                Ad.link_url.ilike(f"%{nd}%"),
            ))
    if page_id:
        stmt = stmt.where(Ad.page_id == page_id)
    if page_name:
        stmt = stmt.where(Ad.page_name.ilike(f"%{page_name}%"))
    if link_contains:
        stmt = stmt.where(Ad.link_url.ilike(f"%{link_contains}%"))
    if language:
        stmt = stmt.where(Ad.language == language)
    if lead_form is not None:
        stmt = stmt.where(Ad.lead_form.is_(lead_form))
    if app_store:
        stmt = stmt.where(Ad.app_store == app_store)
    if ecom_platform:
        stmt = stmt.where(Ad.ecom_platform == ecom_platform)
    if ip:
        stmt = stmt.where(Ad.ip == ip)
    if partner:
        # ads связываются с partner через parsing_configs (keyword, country)
        cfg_sub = (
            select(ParsingConfig.keyword, ParsingConfig.country)
            .where(ParsingConfig.partner.in_(partner))
        )
        stmt = stmt.where(tuple_(Ad.keyword, Ad.country).in_(cfg_sub))
    if country_count is not None:
        # число стран показа берём из eu_countries (доступно только для ЕС-данных)
        stmt = stmt.where(
            func.coalesce(func.array_length(Ad.eu_countries, 1), 0) == country_count
        )
    if is_active is not None:
        stmt = stmt.where(Ad.is_active.is_(is_active))
    if days_active_min is not None:
        stmt = stmt.where(Ad.days_active >= days_active_min)
    if days_active_max is not None:
        stmt = stmt.where(Ad.days_active <= days_active_max)
    if started_from:
        stmt = stmt.where(Ad.started_at >= started_from)
    if started_to:
        stmt = stmt.where(Ad.started_at <= started_to)
    if last_seen_from:
        stmt = stmt.where(Ad.last_seen_at >= last_seen_from)
    if last_seen_to:
        stmt = stmt.where(Ad.last_seen_at <= last_seen_to)
    if search:
        fields = [Ad.body, Ad.title, Ad.caption, Ad.page_name, Ad.library_id, Ad.display_url]
        if search_mode == "broad":
            # широкий: совпадает любое слово из запроса
            words = [w for w in search.split() if w]
            groups = [or_(*[f.ilike(f"%{w}%") for f in fields]) for w in words] or [
                or_(*[f.ilike(f"%{search}%") for f in fields])
            ]
            stmt = stmt.where(or_(*groups))
        else:
            # точный: вся фраза как подстрока
            like = f"%{search}%"
            stmt = stmt.where(or_(*[f.ilike(like) for f in fields]))
    if reach_min is not None:
        stmt = stmt.where(Ad.reach >= reach_min)
    if spend_min is not None:
        stmt = stmt.where(Ad.spend_estimate >= spend_min)
    if eu_country:
        stmt = stmt.where(Ad.eu_countries.op("&&")(eu_country))
    if used_in_ads_min is not None:
        stmt = stmt.where(Ad.used_in_ads_count >= used_in_ads_min)
    return stmt


@router.get("", response_model=list[AdOut])
async def list_feed(
    country: str | None = Query(None),
    countries: list[str] | None = Query(None),
    keyword: str | None = Query(None),
    vertical: str | None = Query(None),
    media_type: str | None = Query(None),
    cta: str | None = Query(None),
    platforms: list[str] | None = Query(None),
    domain: str | None = Query(None),
    page_id: str | None = Query(None),
    page_name: str | None = Query(None),
    link_contains: str | None = Query(None),
    language: str | None = Query(None),
    lead_form: bool | None = Query(None),
    app_store: str | None = Query(None),
    ecom_platform: str | None = Query(None),
    ip: str | None = Query(None),
    search: str | None = Query(None),
    search_mode: str = Query("exact", regex="^(exact|broad)$"),
    partner: list[str] | None = Query(None),
    country_count: int | None = Query(None),
    is_active: bool | None = Query(None),
    days_active_min: int | None = Query(None),
    days_active_max: int | None = Query(None),
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    last_seen_from: datetime | None = Query(None),
    last_seen_to: datetime | None = Query(None),
    reach_min: int | None = Query(None),
    spend_min: int | None = Query(None),
    eu_country: list[str] | None = Query(None),
    used_in_ads_min: int | None = Query(None),
    sort: str = Query("newest", regex="^(newest|oldest|days_desc|days_asc)$"),
    limit: int = Query(40, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    stmt = (
        select(Ad)
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(ModerationEntry.status == ModerationStatus.APPROVED)
        .options(selectinload(Ad.creatives))
    )

    stmt = _apply_ad_filters(
        stmt,
        country=country, countries=countries, keyword=keyword, vertical=vertical,
        media_type=media_type, cta=cta, platforms=platforms, domain=domain,
        page_id=page_id, page_name=page_name, link_contains=link_contains,
        language=language, lead_form=lead_form, app_store=app_store,
        ecom_platform=ecom_platform, ip=ip, search=search, search_mode=search_mode,
        partner=partner, country_count=country_count, is_active=is_active,
        days_active_min=days_active_min, days_active_max=days_active_max,
        started_from=started_from, started_to=started_to,
        last_seen_from=last_seen_from, last_seen_to=last_seen_to,
        reach_min=reach_min, spend_min=spend_min, eu_country=eu_country,
        used_in_ads_min=used_in_ads_min,
    )

    if sort == "newest":
        stmt = stmt.order_by(Ad.first_seen_at.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Ad.first_seen_at.asc())
    elif sort == "days_desc":
        stmt = stmt.order_by(Ad.days_active.desc())
    elif sort == "days_asc":
        stmt = stmt.order_by(Ad.days_active.asc())

    # Дедупликация по phash на уровне SQL.
    # 1) Берём список "репрезентативных" ad_id: для каждого phash оставляем
    #    самую свежую ad, ads без phash — все остаются как есть.
    from sqlalchemy import literal, case, func as sa_func

    base_subq = stmt.subquery()

    # phash первого creative для каждой ad
    first_phash_subq = (
        select(
            Creative.ad_id,
            sa_func.min(Creative.phash).label("phash"),
        )
        .where(Creative.phash.is_not(None))
        .group_by(Creative.ad_id)
        .subquery()
    )

    ad_with_phash = (
        select(
            base_subq.c.id.label("ad_id"),
            base_subq.c.first_seen_at,
            base_subq.c.days_active,
            first_phash_subq.c.phash,
        )
        .select_from(
            base_subq.outerjoin(
                first_phash_subq, base_subq.c.id == first_phash_subq.c.ad_id
            )
        )
        .subquery()
    )

    # Для каждого phash оставляем самую свежую ad (DISTINCT ON по phash).
    # ads без phash — попадают все (NULL уникален в DISTINCT ON).
    representatives_subq = (
        select(ad_with_phash.c.ad_id)
        .distinct(
            sa_func.coalesce(
                ad_with_phash.c.phash, sa_func.cast(ad_with_phash.c.ad_id, String)
            )
        )
        .order_by(
            sa_func.coalesce(
                ad_with_phash.c.phash, sa_func.cast(ad_with_phash.c.ad_id, String)
            ),
            ad_with_phash.c.first_seen_at.desc(),
        )
        .subquery()
    )

    # Считаем дубли для каждого репрезентанта.
    dupe_count_subq = (
        select(
            ad_with_phash.c.phash,
            (sa_func.count() - 1).label("dupes"),
        )
        .where(ad_with_phash.c.phash.is_not(None))
        .group_by(ad_with_phash.c.phash)
        .subquery()
    )

    # Финальная выборка с пагинацией.
    final_stmt = (
        select(Ad)
        .join(representatives_subq, Ad.id == representatives_subq.c.ad_id)
        .options(selectinload(Ad.creatives))
    )
    if sort == "newest":
        final_stmt = final_stmt.order_by(Ad.first_seen_at.desc())
    elif sort == "oldest":
        final_stmt = final_stmt.order_by(Ad.first_seen_at.asc())
    elif sort == "days_desc":
        final_stmt = final_stmt.order_by(Ad.days_active.desc())
    elif sort == "days_asc":
        final_stmt = final_stmt.order_by(Ad.days_active.asc())

    final_stmt = final_stmt.limit(limit).offset(offset)
    items = list((await session.execute(final_stmt)).scalars().all())

    # Подтягиваем duplicates_count
    phashes_in_page = []
    for ad in items:
        ph = next((c.phash for c in ad.creatives if c.phash), None)
        if ph:
            phashes_in_page.append(ph)

    dupe_map: dict[str, int] = {}
    if phashes_in_page:
        rows = (await session.execute(
            select(Creative.phash, sa_func.count(Creative.id))
            .where(Creative.phash.in_(phashes_in_page))
            .group_by(Creative.phash)
        )).all()
        dupe_map = {ph: cnt - 1 for ph, cnt in rows}

    for ad in items:
        ph = next((c.phash for c in ad.creatives if c.phash), None)
        ad.duplicates_count = dupe_map.get(ph, 0) if ph else 0

    # Partner lookup via parsing_configs (keyword + country)
    _keywords = list({ad.keyword for ad in items if ad.keyword})
    _partner_map: dict[tuple, str | None] = {}
    if _keywords:
        _cfg_rows = (await session.execute(
            select(ParsingConfig.keyword, ParsingConfig.country, ParsingConfig.partner)
            .where(ParsingConfig.keyword.in_(_keywords))
        )).all()
        _partner_map = {(r.keyword, r.country): r.partner for r in _cfg_rows}
    for ad in items:
        ad.partner = _partner_map.get((ad.keyword, ad.country)) if ad.keyword else None

    return items


@router.get("/count")
async def feed_count(
    country: str | None = Query(None),
    countries: list[str] | None = Query(None),
    keyword: str | None = Query(None),
    vertical: str | None = Query(None),
    media_type: str | None = Query(None),
    cta: str | None = Query(None),
    platforms: list[str] | None = Query(None),
    domain: str | None = Query(None),
    page_id: str | None = Query(None),
    page_name: str | None = Query(None),
    link_contains: str | None = Query(None),
    language: str | None = Query(None),
    lead_form: bool | None = Query(None),
    app_store: str | None = Query(None),
    ecom_platform: str | None = Query(None),
    ip: str | None = Query(None),
    search: str | None = Query(None),
    search_mode: str = Query("exact", regex="^(exact|broad)$"),
    partner: list[str] | None = Query(None),
    country_count: int | None = Query(None),
    is_active: bool | None = Query(None),
    days_active_min: int | None = Query(None),
    days_active_max: int | None = Query(None),
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    last_seen_from: datetime | None = Query(None),
    last_seen_to: datetime | None = Query(None),
    reach_min: int | None = Query(None),
    spend_min: int | None = Query(None),
    eu_country: list[str] | None = Query(None),
    used_in_ads_min: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    """Кол-во объявлений после фильтров (с учётом phash-дедупликации)."""
    base = (
        select(Ad.id)
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(ModerationEntry.status == ModerationStatus.APPROVED)
    )
    base = _apply_ad_filters(
        base,
        country=country, countries=countries, keyword=keyword, vertical=vertical,
        media_type=media_type, cta=cta, platforms=platforms, domain=domain,
        page_id=page_id, page_name=page_name, link_contains=link_contains,
        language=language, lead_form=lead_form, app_store=app_store,
        ecom_platform=ecom_platform, ip=ip, search=search, search_mode=search_mode,
        partner=partner, country_count=country_count, is_active=is_active,
        days_active_min=days_active_min, days_active_max=days_active_max,
        started_from=started_from, started_to=started_to,
        last_seen_from=last_seen_from, last_seen_to=last_seen_to,
        reach_min=reach_min, spend_min=spend_min, eu_country=eu_country,
        used_in_ads_min=used_in_ads_min,
    )
    filtered = base.subquery()

    first_phash_subq = (
        select(Creative.ad_id, func.min(Creative.phash).label("phash"))
        .where(Creative.phash.is_not(None))
        .group_by(Creative.ad_id)
        .subquery()
    )
    count_stmt = (
        select(
            func.count(
                distinct(
                    func.coalesce(
                        first_phash_subq.c.phash, cast(filtered.c.id, String)
                    )
                )
            )
        )
        .select_from(
            filtered.outerjoin(
                first_phash_subq, filtered.c.id == first_phash_subq.c.ad_id
            )
        )
    )
    total = (await session.execute(count_stmt)).scalar_one()
    return {"total": total}


@router.get("/partners")
async def feed_partners(
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    rows = (await session.execute(
        select(distinct(ParsingConfig.partner))
        .where(ParsingConfig.partner.is_not(None), ParsingConfig.partner != "")
        .order_by(ParsingConfig.partner)
    )).scalars().all()
    return {"partners": list(rows)}


@router.get("/vertical-counts")
async def vertical_counts(
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    rows = (await session.execute(
        select(Ad.vertical, func.count(Ad.id))
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(ModerationEntry.status == ModerationStatus.APPROVED)
        .group_by(Ad.vertical)
    )).all()
    return {"counts": {(v or "unknown"): c for v, c in rows}}


@router.get("/facets")
async def facets(
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    base = (
        select(Ad)
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(ModerationEntry.status == ModerationStatus.APPROVED)
        .subquery()
    )

    countries = (await session.execute(
        select(base.c.country).distinct().order_by(base.c.country)
    )).scalars().all()
    keywords = (await session.execute(
        select(base.c.keyword).distinct().where(base.c.keyword.is_not(None)).order_by(base.c.keyword)
    )).scalars().all()
    verticals = (await session.execute(
        select(base.c.vertical).distinct().where(base.c.vertical.is_not(None)).order_by(base.c.vertical)
    )).scalars().all()
    media_types = (await session.execute(
        select(base.c.media_type).distinct().order_by(base.c.media_type)
    )).scalars().all()
    ctas = (await session.execute(
        select(base.c.cta_text).distinct().where(base.c.cta_text.is_not(None)).order_by(base.c.cta_text)
    )).scalars().all()

    languages = (await session.execute(
        select(base.c.language).distinct().where(base.c.language.is_not(None)).order_by(base.c.language)
    )).scalars().all()
    app_stores = (await session.execute(
        select(base.c.app_store).distinct().where(base.c.app_store.is_not(None)).order_by(base.c.app_store)
    )).scalars().all()
    ecom_platforms = (await session.execute(
        select(base.c.ecom_platform).distinct().where(base.c.ecom_platform.is_not(None)).order_by(base.c.ecom_platform)
    )).scalars().all()

    # platforms — array column, разворачиваем через unnest
    platforms_rows = (await session.execute(
        text(
            "SELECT DISTINCT unnest(a.platforms) AS p "
            "FROM ads a "
            "JOIN moderation_queue m ON m.ad_id = a.id "
            "WHERE m.status = 'APPROVED' AND a.platforms IS NOT NULL "
            "ORDER BY p"
        )
    )).scalars().all()

    return {
        "countries": list(countries),
        "keywords": list(keywords),
        "verticals": list(verticals),
        "media_types": list(media_types),
        "ctas": list(ctas),
        "languages": list(languages),
        "app_stores": list(app_stores),
        "ecom_platforms": list(ecom_platforms),
        "platforms": list(platforms_rows),
    }


@router.get("/{ad_id}", response_model=AdOut)
async def get_ad(
    ad_id: int,
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    stmt = (
        select(Ad)
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(Ad.id == ad_id, ModerationEntry.status == ModerationStatus.APPROVED)
        .options(selectinload(Ad.creatives))
    )
    ad = (await session.execute(stmt)).scalar_one_or_none()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")

    phashes = {c.phash for c in ad.creatives if c.phash}
    max_dupes = 0
    if phashes:
        rows = (await session.execute(
            select(Creative.phash, func.count(Creative.id))
            .where(Creative.phash.in_(phashes))
            .group_by(Creative.phash)
        )).all()
        for _ph, cnt in rows:
            if cnt - 1 > max_dupes:
                max_dupes = cnt - 1
    ad.duplicates_count = max_dupes

    if ad.keyword:
        _cfg = (await session.execute(
            select(ParsingConfig.partner)
            .where(ParsingConfig.keyword == ad.keyword, ParsingConfig.country == ad.country)
        )).scalar_one_or_none()
        ad.partner = _cfg
    else:
        ad.partner = None

    return ad


@router.get("/{ad_id}/similar", response_model=list[AdOut])
async def similar_ads(
    ad_id: int,
    by: str = Query("fp", regex="^(fp|domain|ip)$"),
    limit: int = Query(12, le=50),
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    base = (await session.execute(
        select(Ad)
        .options(selectinload(Ad.creatives))
        .where(Ad.id == ad_id)
    )).scalar_one_or_none()
    if not base:
        raise HTTPException(status_code=404, detail="Ad not found")

    conditions = []

    if by == "fp":
        phashes = {c.phash for c in base.creatives if c.phash}
        if base.page_id:
            conditions.append(Ad.page_id == base.page_id)
        if phashes:
            sub = select(Creative.ad_id).where(Creative.phash.in_(phashes))
            conditions.append(Ad.id.in_(sub))
    elif by == "ip":
        if base.ip:
            conditions.append(Ad.ip == base.ip)
    else:  # by == "domain"
        # домен берём из display_url, а если пусто — из link_url
        nd = _normalize_domain(base.display_url) or _normalize_domain(
            _host_from_url(base.link_url)
        )
        if nd:
            conditions.append(or_(
                Ad.display_url.ilike(f"%{nd}%"),
                Ad.link_url.ilike(f"%{nd}%"),
            ))

    if not conditions:
        return []

    stmt = (
        select(Ad)
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(
            Ad.id != ad_id,
            ModerationEntry.status == ModerationStatus.APPROVED,
            or_(*conditions),
        )
        .options(selectinload(Ad.creatives))
        .order_by(Ad.first_seen_at.desc())
        .limit(limit)
    )
    items = list((await session.execute(stmt)).scalars().all())

    for ad in items:
        ad.duplicates_count = 0

    return items