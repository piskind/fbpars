"""Replace the global UNIQUE(ads.library_id) with a composite UNIQUE(library_id, country).

FB runs the same archive_id (library_id) across countries. With library_id globally unique,
an ad first collected under one country (e.g. PE, auto-APPROVED) shadowed the same ad
collected under another (e.g. MX): the dedup lookup resolved to the PE row, saw it reviewed,
and dropped it as skipped_already_rejected — so MX only ever kept its exclusives. Storing one
row per (library_id, country) makes each country count/serve its own ads.

Idempotent and safe on existing data: the current global-unique guarantees no two rows share a
library_id, so (library_id, country) is already unique — the new index builds without conflict.
"""
import asyncpg, asyncio, os


async def migrate():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        # 1) Composite unique first, so uniqueness is never unenforced mid-migration.
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_library_country ON ads (library_id, country);"
        )
        # 2) Drop the old global-unique on library_id, in whichever form it exists:
        #    a unique index (SQLAlchemy `unique=True, index=True` → ix_ads_library_id) or a
        #    table constraint (ads_library_id_key). Both IF EXISTS → safe to re-run.
        await conn.execute("ALTER TABLE ads DROP CONSTRAINT IF EXISTS ads_library_id_key;")
        await conn.execute("DROP INDEX IF EXISTS ix_ads_library_id;")
        # 3) Recreate a NON-unique index on library_id for the per-country dedup lookup
        #    (kept separate from the composite so a bare library_id filter stays indexed).
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_ads_library_id ON ads (library_id);"
        )
        print("Migration OK: UNIQUE(library_id, country) in place; global library_id unique removed")
    finally:
        await conn.close()


asyncio.run(migrate())
