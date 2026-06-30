import asyncpg, asyncio, os

async def migrate():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("ALTER TABLE parsing_configs ADD COLUMN IF NOT EXISTS is_targeted_country BOOLEAN;")
        print("Migration OK: is_targeted_country added")
    finally:
        await conn.close()

asyncio.run(migrate())
