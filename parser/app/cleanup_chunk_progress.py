"""Manual, idempotent cleanup of orphaned chunk_progress rows (C1).

Editing a config's date range (e.g. date_to 02-01 → 02-02) re-slices its chunks, but the OLD
cursor rows stay behind as garbage — so weekly rows sit next to daily ones, and a stale
01-29..02-01 next to 01-29..02-02. This deletes chunk_progress rows whose (date_from, date_to)
key no longer matches the config's CURRENT slicing (settings.chunk_days over the config's
configured date range), plus rows for configs that no longer exist.

Safe by construction:
  * Only ever touches chunk_progress — NEVER ads / creatives / moderation.
  * Uses the config's RAW date_from/date_to (not the auto_date_from shift), so bookmarks for
    already-collected earlier days inside the window are KEPT (not re-collected).
  * DRY-RUN by default — prints what it would delete. Pass --apply to actually delete.

    python -m app.cleanup_chunk_progress            # dry run (default)
    python -m app.cleanup_chunk_progress --apply     # perform the deletion
"""
import argparse
import asyncio
from datetime import date

from sqlalchemy import select, delete

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import ParsingConfig, ChunkProgress, chunk_key
from app.worker import split_date_range


def _valid_keys(config: ParsingConfig) -> set[tuple[date, date]]:
    """Chunk keys the current config slicing would produce (mirrors coordinator._chunks_for
    but WITHOUT the auto_date_from shift, so in-window historical bookmarks stay valid)."""
    df, dt = config.date_from, config.date_to
    use_split = (
        config.config_type in ("filters", "fanpage")
        and df is not None and dt is not None and dt > df
    )
    if use_split:
        return {chunk_key(a, b) for (a, b) in split_date_range(df, dt, chunk_days=settings.chunk_days)}
    return {chunk_key(df, dt)}


async def main(apply: bool) -> None:
    async with AsyncSessionLocal() as session:
        configs = list((await session.execute(select(ParsingConfig))).scalars().all())
        valid_by_config = {c.id: _valid_keys(c) for c in configs}
        known_config_ids = set(valid_by_config)

        rows = list((await session.execute(select(ChunkProgress))).scalars().all())

        orphans = []  # (config_id, date_from, date_to, reason)
        for r in rows:
            if r.config_id not in known_config_ids:
                orphans.append((r.config_id, r.date_from, r.date_to, "config gone"))
            elif (r.date_from, r.date_to) not in valid_by_config[r.config_id]:
                orphans.append((r.config_id, r.date_from, r.date_to, "not in current slicing"))

        print(f"chunk_progress rows: {len(rows)} total, {len(orphans)} orphaned")
        for cid, df, dt, reason in orphans:
            print(f"  #{cid} {df}..{dt}  [{reason}]")

        if not orphans:
            print("Nothing to clean.")
            return

        if not apply:
            print(f"\nDRY RUN — {len(orphans)} row(s) would be deleted. Re-run with --apply to delete.")
            return

        for cid, df, dt, _reason in orphans:
            await session.execute(
                delete(ChunkProgress).where(
                    ChunkProgress.config_id == cid,
                    ChunkProgress.date_from == df,
                    ChunkProgress.date_to == dt,
                )
            )
        await session.commit()
        print(f"\nDeleted {len(orphans)} orphaned chunk_progress row(s). ads untouched.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean orphaned chunk_progress cursor rows (C1)")
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
