from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import get_current_admin
from app.models_proxy import Ad, ModerationEntry, ModerationStatus, ParsingConfig


router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def dashboard_stats(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    total_ads = (await session.execute(select(func.count(Ad.id)))).scalar_one()
    active_ads = (await session.execute(
        select(func.count(Ad.id)).where(Ad.is_active.is_(True))
    )).scalar_one()

    mod_rows = (await session.execute(
        select(ModerationEntry.status, func.count()).group_by(ModerationEntry.status)
    )).all()
    by_status = {row[0].value: row[1] for row in mod_rows}

    geo_rows = (await session.execute(
        select(Ad.country, func.count(Ad.id).label("n"))
        .group_by(Ad.country)
        .order_by(func.count(Ad.id).desc())
        .limit(10)
    )).all()
    by_geo = [{"country": r.country, "count": r.n} for r in geo_rows]

    kw_rows = (await session.execute(
        select(Ad.keyword, func.count(Ad.id).label("n"))
        .where(Ad.keyword.is_not(None))
        .group_by(Ad.keyword)
        .order_by(func.count(Ad.id).desc())
        .limit(10)
    )).all()
    by_keyword = [{"keyword": r.keyword, "count": r.n} for r in kw_rows]

    active_configs = (await session.execute(
        select(func.count(ParsingConfig.id)).where(ParsingConfig.is_active.is_(True))
    )).scalar_one()

    return {
        "total_ads": total_ads,
        "active_ads": active_ads,
        "pending": by_status.get("pending", 0),
        "approved": by_status.get("approved", 0),
        "rejected": by_status.get("rejected", 0),
        "active_configs": active_configs,
        "by_geo": by_geo,
        "by_keyword": by_keyword,
    }
