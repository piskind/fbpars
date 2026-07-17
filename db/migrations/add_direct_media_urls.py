import asyncpg, asyncio, os

async def migrate():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("ALTER TABLE ads ADD COLUMN IF NOT EXISTS image_urls TEXT[];")
        await conn.execute("ALTER TABLE ads ADD COLUMN IF NOT EXISTS video_urls TEXT[];")
        await conn.execute("ALTER TABLE ads ADD COLUMN IF NOT EXISTS poster_urls TEXT[];")
        print("Migration OK: ads.image_urls/video_urls/poster_urls added")
    finally:
        await conn.close()

asyncio.run(migrate())
