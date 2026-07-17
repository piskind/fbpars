"""Idempotent: add sort_mode/sort_direction and composite unique on parsing_configs.

Safe to re-run. Avoids ADD CONSTRAINT IF NOT EXISTS (invalid PG syntax).
"""
import asyncpg
import asyncio
import os


async def migrate():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("""
            ALTER TABLE parsing_configs
            ADD COLUMN IF NOT EXISTS sort_mode VARCHAR(32) DEFAULT 'total_impressions';
        """)
        await conn.execute("""
            ALTER TABLE parsing_configs
            ADD COLUMN IF NOT EXISTS sort_direction VARCHAR(8) DEFAULT 'desc';
        """)
        await conn.execute("""
            ALTER TABLE parsing_configs
            DROP CONSTRAINT IF EXISTS uq_keyword_country;
        """)
        # Catalog check — PG has no ADD CONSTRAINT IF NOT EXISTS.
        # NULLS NOT DISTINCT needs PG 15+ (compose uses postgres:16).
        exists = await conn.fetchval(
            """
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_keyword_country_sort'
            """
        )
        if not exists:
            await conn.execute("""
                ALTER TABLE parsing_configs
                ADD CONSTRAINT uq_keyword_country_sort
                UNIQUE NULLS NOT DISTINCT (keyword, country, sort_mode, sort_direction);
            """)
        print("Migration OK: sort_mode/sort_direction added, constraint updated")
    finally:
        await conn.close()


asyncio.run(migrate())
