from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.deps import get_current_admin
from app.models_proxy import ParserRun, ParsingConfig
from app.schemas import ParserRunOut, ParserStatusOut, ParserStartIn, ParserReloadIn

router = APIRouter(prefix="/api/parser", tags=["parser"])

# Незакрытые статусы + допустимые режимы запуска.
_ACTIVE = ["triggered", "running"]
_VALID_MODES = {"keyword", "filters", "all"}


async def _active_run(session: AsyncSession) -> ParserRun | None:
    """Последний незакрытый ран (running/triggered).

    ВАЖНО: через .first(), а НЕ scalar_one_or_none() — незакрытых ранов легко бывает
    несколько (очередь triggered), и scalar_one_or_none падал с 'Multiple rows found'.
    Это и был баг, из-за которого /start и /cancel отваливались.
    """
    return (await session.execute(
        select(ParserRun)
        .where(ParserRun.status.in_(_ACTIVE))
        .order_by(ParserRun.triggered_at.desc())
        .limit(1)
    )).scalars().first()


@router.get("/status", response_model=ParserStatusOut)
async def parser_status(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    try:
        recent = list((await session.execute(
            select(ParserRun).order_by(ParserRun.triggered_at.desc()).limit(10)
        )).scalars().all())
    except Exception:
        return ParserStatusOut(running=False, last_run=None, recent_runs=[])

    running_run = next((r for r in recent if r.status in _ACTIVE), None)
    last_run = next((r for r in recent if r.status in ("done", "failed")), None)

    async def _count(*conds) -> int:
        return (await session.execute(
            select(func.count()).select_from(ParsingConfig)
            .where(ParsingConfig.is_active.is_(True), *conds)
        )).scalar() or 0

    try:
        active_keyword = await _count(ParsingConfig.config_type == "keyword")
        active_filters = await _count(ParsingConfig.config_type.in_(["filters", "fanpage"]))
    except Exception:
        active_keyword = active_filters = 0

    return ParserStatusOut(
        running=running_run is not None,
        current_mode=running_run.mode if running_run else None,
        last_run=last_run,
        recent_runs=recent,
        active_keyword=active_keyword,
        active_filters=active_filters,
    )


@router.post("/start", response_model=ParserRunOut)
async def start_parser(
    payload: ParserStartIn | None = None,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """Запустить прогон в выбранном режиме: keyword / filters / all.

    Рестарт контейнера НЕ нужен — координатор поднимает triggered-ран поллером за ~15с.
    """
    mode = (payload.mode if payload else "all") or "all"
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail=f"Неизвестный режим: {mode}")

    try:
        active = await _active_run(session)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB unavailable: {e}")

    if active:
        raise HTTPException(status_code=409, detail="Парсер уже запущен или ожидает запуска")

    run = ParserRun(status="triggered", mode=mode)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@router.post("/reload", response_model=ParserRunOut)
async def reload_configs(
    payload: ParserReloadIn | None = None,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """«Подхватить <тип>»: до-собрать только что включённые конфиги указанного типа
    (keyword / filters / all) без остановки прогона. Ставим reload_mode — координатор на
    следующей итерации до-enqueue'ит новые конфиги этого типа в текущий ран."""
    mode = (payload.mode if payload else "all") or "all"
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail=f"Неизвестный тип: {mode}")

    active = await _active_run(session)
    if not active:
        raise HTTPException(status_code=404, detail="Нет активного запуска — подхватывать нечего")
    active.reload_mode = mode
    await session.commit()
    await session.refresh(active)
    return active


@router.post("/cancel", response_model=ParserRunOut)
async def cancel_parser(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """Отменить сбор: гасим ВСЕ незакрытые раны (и running, и очередь triggered)."""
    try:
        actives = list((await session.execute(
            select(ParserRun)
            .where(ParserRun.status.in_(_ACTIVE))
            .order_by(ParserRun.triggered_at.desc())
        )).scalars().all())
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB unavailable: {e}")

    if not actives:
        raise HTTPException(status_code=404, detail="Нет активных запусков")

    now = datetime.now(timezone.utc)
    for a in actives:
        a.status = "cancelled"
        a.finished_at = now
    await session.commit()
    await session.refresh(actives[0])
    return actives[0]


@router.get("/logs", response_model=dict)
async def parser_logs(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    try:
        run = (await session.execute(
            select(ParserRun)
            .where(ParserRun.log_tail.is_not(None))
            .order_by(ParserRun.triggered_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    except Exception:
        return {"log_tail": ""}

    return {"log_tail": run.log_tail if run else ""}
