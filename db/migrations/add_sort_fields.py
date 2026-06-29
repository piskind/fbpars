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
        # NULLS NOT DISTINCT: NULL values treated as equal (PG 15+).
        # Prevents duplicate (keyword=NULL, country, sort_mode, sort_direction) rows.
        await conn.execute("""
            ALTER TABLE parsing_configs
            ADD CONSTRAINT IF NOT EXISTS uq_keyword_country_sort
            UNIQUE NULLS NOT DISTINCT (keyword, country, sort_mode, sort_direction);
        """)
        print("Migration OK: sort_mode/sort_direction added, constraint updated")
    finally:
        await conn.close()


asyncio.run(migrate())
