from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from app.config import settings
from app.routers import auth, configs, moderation, users, media, stats, client_auth, feed, parser


async def _ensure_schema():
    """Apply missing DDL that alembic may not have run yet (no alembic in api container)."""
    from app.db import engine
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS parser_runs (
                id SERIAL PRIMARY KEY,
                triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                status VARCHAR(16) NOT NULL DEFAULT 'triggered',
                stats JSONB,
                log_tail TEXT
            )
        """))
        await conn.execute(text(
            "ALTER TABLE parsing_configs ADD COLUMN IF NOT EXISTS partner TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE parsing_configs ADD COLUMN IF NOT EXISTS category TEXT"
        ))


async def _progret_schetchik() -> None:
    """Посчитать общее число объявлений заранее, чтобы первый заход не ждал 13 секунд."""
    import asyncio
    import time as _time
    from sqlalchemy import func, select
    from app.db import AsyncSessionLocal
    from app.models_proxy import Ad, ModerationEntry
    from app.routers import feed as _feed

    await asyncio.sleep(2)  # дать поднятому приложению отдать healthcheck
    try:
        async with AsyncSessionLocal() as session:
            vsego = (await session.execute(
                select(func.count())
                .select_from(Ad)
                .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
                .where(ModerationEntry.status == "APPROVED", Ad.is_dup.is_(False))
            )).scalar_one()
        _feed._TOTAL_KESH["znachenie"] = vsego
        _feed._TOTAL_KESH["do"] = _time.time() + _feed._TOTAL_TTL
        logger.info(f"счётчик главной прогрет: {vsego}")

        # Разбивка по вертикалям — ещё 37 с на первый заход, греем сразу.
        from sqlalchemy import case, or_
        async with AsyncSessionLocal() as session:
            _vert = case(
                (or_(_feed._is_broad_filter_ad(), Ad.vertical == "general"), None),
                else_=Ad.vertical,
            )
            rows = (await session.execute(
                select(_vert.label("v"), func.count())
                .select_from(Ad)
                .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
                .where(ModerationEntry.status == "APPROVED", Ad.is_dup.is_(False))
                .group_by(_vert)
            )).all()
        _feed._VERT_KESH["dannye"] = {(v or "unknown"): c for v, c in rows}
        _feed._VERT_KESH["do"] = _time.time() + _feed._TOTAL_TTL
        logger.info(f"вертикали прогреты: {_feed._VERT_KESH['dannye']}")

        # Админские страницы: дашборд 48 с, настройки 13 с на первом открытии.
        # Дёргаем их по localhost — прогревается настоящий кеш этих обработчиков.
        import urllib.request
        from app.security import create_token
        _tok = create_token(1)
        for _put, _imya in (("/api/stats", "дашборд"), ("/api/configs", "настройки")):
            try:
                _zapros = urllib.request.Request(
                    "http://127.0.0.1:8000" + _put,
                    headers={"Authorization": f"Bearer {_tok}"})
                await asyncio.to_thread(
                    lambda: urllib.request.urlopen(_zapros, timeout=300).read())
                logger.info(f"{_imya} прогрет")
            except Exception as _e:
                logger.warning(f"{_imya} не прогрелся: {type(_e).__name__}: {_e}")
    except Exception as exc:
        logger.warning(f"прогрев счётчика не удался: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    await _ensure_schema()
    zadacha = asyncio.create_task(_progret_schetchik())
    yield
    zadacha.cancel()


app = FastAPI(title="FB Spy API", version="0.1.0", lifespan=lifespan)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(configs.router)
app.include_router(moderation.router)
app.include_router(users.router)
app.include_router(media.router)
app.include_router(stats.router)
app.include_router(client_auth.router)
app.include_router(feed.router)
app.include_router(parser.router)


@app.get("/health")
async def health():
    return {"ok": True}
