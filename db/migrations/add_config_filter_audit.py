"""Idempotent: add parsing_configs.filters_updated_at (C2).

updated_at is bumped on every row write, including the parser stamping last_parsed_at each
run, so it can't record when the FILTERS were last edited. filters_updated_at is set only by
the admin config-edit endpoint when a filtering field changes. Safe to re-run.
"""
import asyncpg, asyncio, os


async def migrate():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "ALTER TABLE parsing_configs ADD COLUMN IF NOT EXISTS filters_updated_at TIMESTAMPTZ;"
        )
        print("Migration OK: parsing_configs.filters_updated_at added")
    finally:
        await conn.close()


asyncio.run(migrate())
