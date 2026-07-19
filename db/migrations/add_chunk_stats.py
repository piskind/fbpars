"""Idempotent: add chunk_progress.last_stats / last_run_id (C3).

Persists each chunk's final 'done' totals to the DB so reporting no longer depends on docker
logs (wiped on image rebuild). Written by the coordinator when a chunk job finishes. Safe to
re-run — no-op if chunk_progress doesn't exist yet (add_chunk_progress runs earlier).
"""
import asyncpg, asyncio, os


async def migrate():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        exists = await conn.fetchval("SELECT to_regclass('public.chunk_progress');")
        if not exists:
            print("Migration SKIP: chunk_progress table not present yet")
            return
        await conn.execute("ALTER TABLE chunk_progress ADD COLUMN IF NOT EXISTS last_stats JSONB;")
        await conn.execute("ALTER TABLE chunk_progress ADD COLUMN IF NOT EXISTS last_run_id INTEGER;")
        print("Migration OK: chunk_progress.last_stats/last_run_id added")
    finally:
        await conn.close()


asyncio.run(migrate())
