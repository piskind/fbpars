from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import auth, configs, moderation, users, media, stats, client_auth, feed, parser


async def _ensure_parser_runs_table():
    """Create parser_runs table if it doesn't exist yet (no alembic in api container)."""
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _ensure_parser_runs_table()
    yield


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
