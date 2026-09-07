import os as _os
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import get_current_admin
from app.models_proxy import Ad, ModerationEntry, ModerationStatus, ParsingConfig


router = APIRouter(prefix="/api/stats", tags=["stats"])

# Сводка собирается ~100 с (шесть агрегатов по 7.4 млн строк), браузер столько
# не ждёт. Держим готовую в памяти, пересчитываем по таймеру.
_STATS_TTL = int(_os.getenv("STATS_TTL", "600") or 600)
_STATS_KESH: dict = {"do": 0.0, "dannye": None}


@router.get("")
async def dashboard_stats(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    # Повторы креатива не учитываем нигде — иначе статистика не сходится с лентой.
    import time as _time
    _tek = _time.time()
    if _STATS_KESH["do"] > _tek and _STATS_KESH["dannye"] is not None:
        return _STATS_KESH["dannye"]

    total_ads = (await session.execute(
        select(func.count(Ad.id)).where(Ad.is_dup == False)  # noqa: E712 — IS false ломает индекс
    )).scalar_one()
    active_ads = (await session.execute(
        select(func.count(Ad.id)).where(Ad.is_active == True, Ad.is_dup == False)  # noqa: E712
    )).scalar_one()

    mod_rows = (await session.execute(
        select(ModerationEntry.status, func.count())
        .join(Ad, Ad.id == ModerationEntry.ad_id)
        .where(Ad.is_dup == False)  # noqa: E712
        .group_by(ModerationEntry.status)
    )).all()
    by_status = {row[0].value: row[1] for row in mod_rows}

    geo_rows = (await session.execute(
        select(Ad.country, func.count(Ad.id).label("n"))
        .where(Ad.is_dup == False)  # noqa: E712
        .group_by(Ad.country)
        .order_by(func.count(Ad.id).desc())
        .limit(10)
    )).all()
    by_geo = [{"country": r.country, "count": r.n} for r in geo_rows]

    kw_rows = (await session.execute(
        select(Ad.keyword, func.count(Ad.id).label("n"))
        .where(Ad.keyword.is_not(None), Ad.is_dup == False)  # noqa: E712
        .group_by(Ad.keyword)
        .order_by(func.count(Ad.id).desc())
        .limit(10)
    )).all()
    by_keyword = [{"keyword": r.keyword, "count": r.n} for r in kw_rows]

    active_configs = (await session.execute(
        select(func.count(ParsingConfig.id)).where(ParsingConfig.is_active.is_(True))
    )).scalar_one()

    _svodka = {
        "total_ads": total_ads,
        "active_ads": active_ads,
        "pending": by_status.get("pending", 0),
        "approved": by_status.get("approved", 0),
        "rejected": by_status.get("rejected", 0),
        "active_configs": active_configs,
        "by_geo": by_geo,
        "by_keyword": by_keyword,
    }
    _STATS_KESH["dannye"] = _svodka
    _STATS_KESH["do"] = _tek + _STATS_TTL
    return _svodka
