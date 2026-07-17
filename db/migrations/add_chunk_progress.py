import asyncpg, asyncio, os

async def migrate():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chunk_progress (
                config_id INTEGER NOT NULL REFERENCES parsing_configs(id) ON DELETE CASCADE,
                date_from DATE NOT NULL,
                date_to DATE NOT NULL,
                last_cursor TEXT,
                collected_count INTEGER NOT NULL DEFAULT 0,
                has_next BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (config_id, date_from, date_to)
            );
        """)
        print("Migration OK: chunk_progress created")
    finally:
        await conn.close()

asyncio.run(migrate())
