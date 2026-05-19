from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.deps import get_current_admin
from app.schemas import ModerationItemOut, ModerationActionIn
from app.models_proxy import ModerationEntry, Ad, ModerationStatus
from datetime import datetime, timezone


router = APIRouter(prefix="/api/moderation", tags=["moderation"])


@router.get("", response_model=list[ModerationItemOut])
async def list_moderation(
    status: str = Query("pending"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    stmt = (
        select(ModerationEntry)
        .options(selectinload(ModerationEntry.ad).selectinload(Ad.creatives))
        .where(ModerationEntry.status == status)
        .order_by(ModerationEntry.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


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
