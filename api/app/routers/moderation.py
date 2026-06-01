from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.deps import get_current_admin
from app.schemas import ModerationItemOut, ModerationActionIn, ModerationListOut
from app.models_proxy import ModerationEntry, Ad, ModerationStatus, Creative
from datetime import datetime, timezone


router = APIRouter(prefix="/api/moderation", tags=["moderation"])


def _apply_filters(stmt, Ad, ModerationEntry, Creative, status, country, keyword, is_active, has_media, search):
    stmt = stmt.where(ModerationEntry.status == status)
    if country:
        stmt = stmt.where(Ad.country == country)
    if keyword:
        stmt = stmt.where(Ad.keyword == keyword)
    if is_active is not None:
        stmt = stmt.where(Ad.is_active.is_(is_active))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            (Ad.body.ilike(like))
            | (Ad.page_name.ilike(like))
            | (Ad.library_id.ilike(like))
            | (Ad.display_url.ilike(like))
        )
    if has_media is True:
        stmt = stmt.where(
            select(Creative.id).where(Creative.ad_id == Ad.id).correlate(Ad).exists()
        )
    elif has_media is False:
        stmt = stmt.where(
            ~select(Creative.id).where(Creative.ad_id == Ad.id).correlate(Ad).exists()
        )
    return stmt


@router.get("", response_model=ModerationListOut)
async def list_moderation(
    status: str = Query("pending"),
    country: str | None = Query(None),
    keyword: str | None = Query(None),
    is_active: bool | None = Query(None),
    has_media: bool | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    base = (
        select(ModerationEntry)
        .join(Ad, ModerationEntry.ad_id == Ad.id)
    )
    base = _apply_filters(base, Ad, ModerationEntry, Creative, status, country, keyword, is_active, has_media, search)

    count_stmt = (
        select(func.count(ModerationEntry.id))
        .join(Ad, ModerationEntry.ad_id == Ad.id)
    )
    count_stmt = _apply_filters(count_stmt, Ad, ModerationEntry, Creative, status, country, keyword, is_active, has_media, search)
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = (
        base
        .options(selectinload(ModerationEntry.ad).selectinload(Ad.creatives))
        .order_by(ModerationEntry.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list((await session.execute(stmt)).scalars().all())

    phashes = set()
    for item in items:
        for c in item.ad.creatives:
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

    for item in items:
        max_dupes = 0
        for c in item.ad.creatives:
            if c.phash and dupe_counts.get(c.phash, 1) - 1 > max_dupes:
                max_dupes = dupe_counts[c.phash] - 1
        item.ad.duplicates_count = max_dupes

    return ModerationListOut(items=items, total=total)


@router.get("/facets")
async def facets(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    countries = (await session.execute(
        select(Ad.country).distinct().order_by(Ad.country)
    )).scalars().all()
    keywords = (await session.execute(
        select(Ad.keyword).distinct().where(Ad.keyword.is_not(None)).order_by(Ad.keyword)
    )).scalars().all()
    return {"countries": list(countries), "keywords": list(keywords)}


@router.get("/stats")
async def stats(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    stmt = select(ModerationEntry.status, func.count()).group_by(ModerationEntry.status)
    rows = (await session.execute(stmt)).all()
    return {s.value: n for s, n in rows}


@router.post("/{entry_id}", response_model=ModerationItemOut)
async def review(
    entry_id: int,
    data: ModerationActionIn,
    session: AsyncSession = Depends(get_session),
    current=Depends(get_current_admin),
):
    if data.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="status must be 'approved' or 'rejected'")

    stmt = (
        select(ModerationEntry)
        .options(selectinload(ModerationEntry.ad).selectinload(Ad.creatives))
        .where(ModerationEntry.id == entry_id)
    )
    entry = (await session.execute(stmt)).scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Not found")

    entry.status = ModerationStatus(data.status)
    entry.reject_reason = data.reject_reason
    entry.reviewed_by = current.id
    entry.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(entry)
    return entry
