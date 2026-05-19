from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.deps import get_current_admin
from app.schemas import ParsingConfigIn, ParsingConfigOut
from app.models_proxy import ParsingConfig


router = APIRouter(prefix="/api/configs", tags=["configs"])


@router.get("", response_model=list[ParsingConfigOut])
async def list_configs(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    stmt = select(ParsingConfig).order_by(ParsingConfig.id)
    return list((await session.execute(stmt)).scalars().all())


@router.post("", response_model=ParsingConfigOut, status_code=201)
async def create_config(
    data: ParsingConfigIn,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    cfg = ParsingConfig(**data.model_dump())
    session.add(cfg)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Duplicate keyword+country or other error")
    await session.refresh(cfg)
    return cfg


@router.patch("/{config_id}", response_model=ParsingConfigOut)
async def update_config(
    config_id: int,
    data: ParsingConfigIn,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    cfg = await session.get(ParsingConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Not found")
    for k, v in data.model_dump().items():
        setattr(cfg, k, v)
    await session.commit()
    await session.refresh(cfg)
    return cfg


@router.delete("/{config_id}", status_code=204)
async def delete_config(
    config_id: int,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    cfg = await session.get(ParsingConfig, config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Not found")
    await session.delete(cfg)
    await session.commit()
