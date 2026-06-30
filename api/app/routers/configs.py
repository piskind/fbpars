from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import get_current_admin
from app.models_proxy import ParsingConfig, Ad
from sqlalchemy.exc import IntegrityError
from app.schemas import ParsingConfigOut, ParsingConfigCreate, ParsingConfigUpdate, BulkConfigIn, BulkConfigOut


router = APIRouter(prefix="/api/configs", tags=["configs"])


def _config_out(cfg: ParsingConfig, ads_count: int = 0, last_parsed_at: datetime | None = None) -> ParsingConfigOut:
    return ParsingConfigOut(
        id=cfg.id,
        keyword=cfg.keyword,
        country=cfg.country,
        vertical=cfg.vertical,
        is_active=cfg.is_active,
        notes=cfg.notes,
        partner=cfg.partner,
        category=cfg.category,
        languages=cfg.languages,
        config_type=cfg.config_type,
        active_status=cfg.active_status,
        media_type_filter=cfg.media_type_filter,
        platforms=cfg.platforms,
        date_from=cfg.date_from,
        date_to=cfg.date_to,
        advertiser=cfg.advertiser,
        auto_date_from_last_parse=cfg.auto_date_from_last_parse,
        is_targeted_country=cfg.is_targeted_country,
        sort_mode=cfg.sort_mode,
        sort_direction=cfg.sort_direction,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
        ads_count=ads_count,
        last_parsed_at=last_parsed_at,
    )


@router.get("", response_model=list[ParsingConfigOut])
async def list_configs(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    cfg_stmt = select(ParsingConfig).order_by(ParsingConfig.id)
    configs = list((await session.execute(cfg_stmt)).scalars().all())

    stats_stmt = (
        select(
            Ad.country,
            Ad.keyword,
            func.count(Ad.id).label("cnt"),
            func.max(Ad.last_seen_at).label("last"),
        )
        .group_by(Ad.country, Ad.keyword)
    )
    rows = (await session.execute(stats_stmt)).all()
    stats = {(r.country, r.keyword): (r.cnt, r.last) for r in rows}

    result = []
    for c in configs:
        cnt, last = stats.get((c.country, c.keyword), (0, None))
        result.append(_config_out(c, ads_count=cnt, last_parsed_at=last))
    return result


@router.post("", response_model=ParsingConfigOut)
async def create_config(
    body: ParsingConfigCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    cfg = ParsingConfig(
        keyword=body.keyword,
        country=body.country,
        vertical=body.vertical,
        is_active=body.is_active,
        notes=body.notes,
        partner=body.partner,
        category=body.category,
        languages=body.languages,
        config_type=body.config_type,
        active_status=body.active_status,
        media_type_filter=body.media_type_filter,
        platforms=body.platforms,
        date_from=body.date_from,
        date_to=body.date_to,
        advertiser=body.advertiser,
        auto_date_from_last_parse=body.auto_date_from_last_parse,
        is_targeted_country=body.is_targeted_country,
        sort_mode=body.sort_mode,
        sort_direction=body.sort_direction,
    )
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return _config_out(cfg)


@router.patch("/{cfg_id}", response_model=ParsingConfigOut)
async def update_config(
    cfg_id: int,
    body: ParsingConfigUpdate,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    cfg = await session.get(ParsingConfig, cfg_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Not found")

    if body.keyword is not None:
        cfg.keyword = body.keyword
    if body.country is not None:
        cfg.country = body.country
    if body.is_active is not None:
        cfg.is_active = body.is_active
    if body.notes is not None:
        cfg.notes = body.notes
    if body.vertical is not None:
        cfg.vertical = body.vertical
    if body.partner is not None:
        cfg.partner = body.partner
    if body.category is not None:
        cfg.category = body.category
    if body.languages is not None:
        cfg.languages = body.languages
    if body.config_type is not None:
        cfg.config_type = body.config_type
    if body.active_status is not None:
        cfg.active_status = body.active_status
    if body.media_type_filter is not None:
        cfg.media_type_filter = body.media_type_filter
    if body.platforms is not None:
        cfg.platforms = body.platforms
    if body.date_from is not None:
        cfg.date_from = body.date_from
    if body.date_to is not None:
        cfg.date_to = body.date_to
    if body.advertiser is not None:
        cfg.advertiser = body.advertiser
    if body.auto_date_from_last_parse is not None:
        cfg.auto_date_from_last_parse = body.auto_date_from_last_parse
    if body.is_targeted_country is not None:
        cfg.is_targeted_country = body.is_targeted_country
    if body.sort_mode is not None:
        cfg.sort_mode = body.sort_mode
    if body.sort_direction is not None:
        cfg.sort_direction = body.sort_direction

    await session.commit()
    await session.refresh(cfg)

    cnt_stmt = select(func.count(Ad.id), func.max(Ad.last_seen_at)).where(
        Ad.country == cfg.country, Ad.keyword == cfg.keyword
    )
    row = (await session.execute(cnt_stmt)).one()

    return _config_out(cfg, ads_count=row[0] or 0, last_parsed_at=row[1])


@router.delete("/{cfg_id}")
async def delete_config(
    cfg_id: int,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    cfg = await session.get(ParsingConfig, cfg_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Not found")
    await session.delete(cfg)
    await session.commit()
    return {"ok": True}


@router.post("/bulk", response_model=BulkConfigOut)
async def bulk_create_configs(
    body: BulkConfigIn,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """
    Create multiple configs from pipe-separated text (one per line).
    Format: country|keyword|sort_mode|sort_direction|vertical|notes
    Defaults: keyword=empty, sort_mode=total_impressions, sort_direction=desc, vertical=nutra
    config_type: 'keyword' if keyword provided, else 'filters'.
    """
    created = 0
    skipped = 0
    for raw_line in body.text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split('|')]
        country = parts[0].upper() if parts[0] else ''
        if not country:
            continue
        keyword = parts[1] if len(parts) > 1 and parts[1] else None
        sort_mode = (parts[2] if len(parts) > 2 and parts[2] else '') or 'total_impressions'
        sort_direction = (parts[3] if len(parts) > 3 and parts[3] else '') or 'desc'
        vertical = (parts[4] if len(parts) > 4 and parts[4] else '') or 'nutra'
        notes = parts[5] if len(parts) > 5 and parts[5] else None
        config_type = 'keyword' if keyword else 'filters'
        try:
            async with session.begin_nested():
                cfg = ParsingConfig(
                    keyword=keyword,
                    country=country,
                    vertical=vertical,
                    is_active=True,
                    notes=notes,
                    config_type=config_type,
                    sort_mode=sort_mode,
                    sort_direction=sort_direction,
                )
                session.add(cfg)
            created += 1
        except IntegrityError:
            skipped += 1
    await session.commit()
    return BulkConfigOut(created=created, skipped=skipped)
