from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.deps import get_current_admin
from app.models_proxy import ParserRun
from app.schemas import ParserRunOut, ParserStatusOut

router = APIRouter(prefix="/api/parser", tags=["parser"])


@router.get("/status", response_model=ParserStatusOut)
async def parser_status(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    recent = list((await session.execute(
        select(ParserRun).order_by(ParserRun.triggered_at.desc()).limit(10)
    )).scalars().all())

    running = any(r.status in ("triggered", "running") for r in recent)
    last_run = next(
        (r for r in recent if r.status in ("done", "failed")),
        None
    )
    return ParserStatusOut(running=running, last_run=last_run, recent_runs=recent)


@router.post("/start", response_model=ParserRunOut)
async def start_parser(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    active = (await session.execute(
        select(ParserRun).where(ParserRun.status.in_(["triggered", "running"]))
    )).scalar_one_or_none()

    if active:
        raise HTTPException(status_code=409, detail="Parser is already running or triggered")

    run = ParserRun(status="triggered")
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@router.get("/logs", response_model=dict)
async def parser_logs(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    run = (await session.execute(
        select(ParserRun)
        .where(ParserRun.log_tail.is_not(None))
        .order_by(ParserRun.triggered_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    return {"log_tail": run.log_tail if run else "Нет логов"}
