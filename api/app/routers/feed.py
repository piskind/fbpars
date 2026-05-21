from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, and_, distinct
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
)
from app.schemas import AdOut


router = APIRouter(prefix="/api/feed", tags=["feed"])


@router.get("", response_model=list[AdOut])
async def list_feed(
    country: str | None = Query(None),
    keyword: str | None = Query(None),
    vertical: str | None = Query(None),
    media_type: str | None = Query(None),
    cta: str | None = Query(None),
    domain: str | None = Query(None),
    page_name: str | None = Query(None),
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    days_active_min: int | None = Query(None),
    days_active_max: int | None = Query(None),
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    sort: str = Query("newest", regex="^(newest|oldest|days_desc|days_asc)$"),
    limit: int = Query(40, le=100),
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

    if country:
        stmt = stmt.where(Ad.country == country)
    if keyword:
        stmt = stmt.where(Ad.keyword == keyword)
    if vertical:
        stmt = stmt.where(Ad.vertical == vertical)
    if media_type:
        stmt = stmt.where(Ad.media_type == media_type)
    if cta:
        stmt = stmt.where(Ad.cta_text.ilike(f"%{cta}%"))
    if domain:
        stmt = stmt.where(Ad.display_url.ilike(f"%{domain}%"))
    if page_name:
        stmt = stmt.where(Ad.page_name.ilike(f"%{page_name}%"))
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
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(
            Ad.body.ilike(like),
            Ad.page_name.ilike(like),
            Ad.library_id.ilike(like),
            Ad.display_url.ilike(like),
        ))

    if sort == "newest":
        stmt = stmt.order_by(Ad.first_seen_at.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Ad.first_seen_at.asc())
    elif sort == "days_desc":
        stmt = stmt.order_by(Ad.days_active.desc())
    elif sort == "days_asc":
        stmt = stmt.order_by(Ad.days_active.asc())

    stmt = stmt.limit(limit).offset(offset)
    items = list((await session.execute(stmt)).scalars().all())

    phashes = set()
    for ad in items:
        for c in ad.creatives:
            if c.phash:
                phashes.add(c.phash)

    dupe_counts: dict[str, int] = {}
    if phashes:
        rows = (await session.execute(
            select(Creative.phash, func.count(Creative.id))
            .where(Creative.phash.in_(phashes))
            .group_by(Creative.phash)
        )).all()
        dupe_counts = {ph: cnt for ph, cnt in rows}

    for ad in items:
        max_dupes = 0
        for c in ad.creatives:
            if c.phash and dupe_counts.get(c.phash, 1) - 1 > max_dupes:
                max_dupes = dupe_counts[c.phash] - 1
        ad.duplicates_count = max_dupes

    return items


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

    return {
        "countries": list(countries),
        "keywords": list(keywords),
        "verticals": list(verticals),
        "media_types": list(media_types),
        "ctas": list(ctas),
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

    return ad


@router.get("/{ad_id}/similar", response_model=list[AdOut])
async def similar_ads(
    ad_id: int,
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

    phashes = {c.phash for c in base.creatives if c.phash}

    conditions = []
    if base.page_id:
        conditions.append(Ad.page_id == base.page_id)
    if phashes:
        sub = (
            select(Creative.ad_id)
            .where(Creative.phash.in_(phashes))
        )
        conditions.append(Ad.id.in_(sub))

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