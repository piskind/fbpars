"""Discovery coordinator (Phase 2).

Fans a discovery run out into RQ chunk jobs, then monitors them to completion and
aggregates their stats — preserving the existing ParserRun trigger lifecycle
(triggered → running → done) that the admin API and scheduler rely on.

One discovery run is monitored at a time (same as the old in-process run_once), but the
actual pagination work now runs in parallel across the parser-worker replicas.
"""
import asyncio
from datetime import date

from loguru import logger

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import ParserRun, ChunkProgress, chunk_key
from app.worker import get_active_configs, split_date_range

_STAT_KEYS = (
    "raw", "new", "updated", "media_ok", "media_fail", "errors",
    "skipped_duplicate", "skipped_no_media", "skipped_already_rejected",
    "skipped_phash_duplicate", "removed_no_media",
)

# RQ terminal statuses (canceled/stopped spellings vary across rq versions).
_TERMINAL = {"finished", "failed", "canceled", "cancelled", "stopped"}


def _chunks_for(config) -> list[tuple[date | None, date | None]]:
    """Mirror worker.process_config's date-splitting decision, but down to chunk_days."""
    effective_date_from = config.date_from
    if config.auto_date_from_last_parse and config.last_parsed_at:
        effective_date_from = config.last_parsed_at.date()

    use_split = (
        config.config_type in ("filters", "fanpage")
        and effective_date_from is not None
        and config.date_to is not None
        and config.date_to > effective_date_from
    )
    if use_split:
        return split_date_range(effective_date_from, config.date_to, chunk_days=settings.chunk_days)
    return [(effective_date_from, config.date_to)]


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


async def enqueue_run(run_id: int) -> list[str]:
    """Enqueue one deduped RQ 'parse' job per (config, date-chunk). Returns job ids."""
    from app.queue import parse_queue
    from app.tasks import parse_chunk
    from rq import Retry
    from rq.job import Job

    q = parse_queue()
    conn = q.connection
    job_ids: list[str] = []
    skipped_done = 0
    resumed = 0

    async with AsyncSessionLocal() as session:
        configs = await get_active_configs(session)

        for config in configs:
            for (df, dt) in _chunks_for(config):
                # Consult the resume bookmark: skip exhausted chunks, resume in-progress
                # ones from their saved cursor, start fresh ones from the first page.
                kdf, kdt = chunk_key(df, dt)
                progress = await session.get(ChunkProgress, (config.id, kdf, kdt))
                if progress is not None and not progress.has_next:
                    skipped_done += 1
                    continue  # FB already exhausted this chunk — nothing left to collect
                cursor_start = progress.last_cursor if progress else None
                if cursor_start:
                    resumed += 1

                # Deterministic id → re-triggering a run doesn't duplicate in-flight chunks.
                jid = f"chunk:{config.id}:{df}:{dt}:{run_id}"
                if Job.exists(jid, connection=conn):
                    continue
                q.enqueue(
                    parse_chunk,
                    kwargs={
                        "config_id": config.id,
                        "date_from": _iso(df),
                        "date_to": _iso(dt),
                        "cursor_start": cursor_start,
                        "run_id": run_id,
                    },
                    job_id=jid,
                    job_timeout=settings.parse_job_timeout,
                    result_ttl=3600,
                    failure_ttl=86400,
                    # A GraphQL failure now fails the chunk (no silent DOM-scroll fallback).
                    # Retry with growing backoff; parse_chunk rotates IP before each retry.
                    retry=Retry(max=3, interval=[60, 180, 300]),
                )
                job_ids.append(jid)

    logger.info(
        f"[coordinator] run #{run_id}: enqueued {len(job_ids)} chunk job(s) "
        f"({resumed} resumed, {skipped_done} skipped as exhausted) across {len(configs)} config(s)"
    )
    return job_ids


async def monitor_run(run_id: int, job_ids: list[str], poll_sec: int = 5) -> dict:
    """Poll chunk jobs until all terminal (or run cancelled); aggregate their stats.

    A crashed/OOM-killed chunk (its work-horse dies without marking the job failed) used
    to leave the job stuck in 'started' forever, hanging the whole coordinator. Each poll
    we advance abandoned/timed-out jobs into the FailedJobRegistry and count anything there
    (or a vanished job) as a failed chunk, so one bad chunk can't block the run.
    """
    from app.queue import parse_queue
    from rq.job import Job
    from rq.registry import FailedJobRegistry, StartedJobRegistry

    q = parse_queue()
    conn = q.connection
    failed_reg = FailedJobRegistry(queue=q)
    started_reg = StartedJobRegistry(queue=q)
    total = {k: 0 for k in _STAT_KEYS}
    pending = set(job_ids)
    last_report = -1

    while pending:
        await asyncio.sleep(poll_sec)

        # Honour admin cancel: stop pending jobs and bail.
        async with AsyncSessionLocal() as session:
            run = await session.get(ParserRun, run_id)
        if run and run.status == "cancelled":
            for jid in list(pending):
                try:
                    Job.fetch(jid, connection=conn).cancel()
                except Exception:
                    pass
            logger.info(f"[coordinator] run #{run_id} cancelled — stopped {len(pending)} pending job(s)")
            return total

        # Move jobs whose work-horse died (OOM / timeout / crash) out of 'started' so we
        # don't wait on them forever; then treat FailedJobRegistry membership as terminal.
        try:
            started_reg.cleanup()
            failed_reg.cleanup()
            failed_ids = set(failed_reg.get_job_ids())
        except Exception as exc:
            logger.warning(f"[coordinator] registry cleanup failed: {exc}")
            failed_ids = set()

        for jid in list(pending):
            try:
                job = Job.fetch(jid, connection=conn)
            except Exception:
                # Job data expired/gone — stop waiting, count it as a failed chunk.
                logger.warning(f"[coordinator] job {jid} vanished — treating as failed")
                total["errors"] = total.get("errors", 0) + 1
                pending.discard(jid)
                continue

            status = job.get_status(refresh=True)
            if jid in failed_ids or status == "failed":
                total["errors"] = total.get("errors", 0) + 1
                tail = (job.exc_info or "")[-300:]
                logger.warning(f"[coordinator] job {jid} failed: {tail}")
                pending.discard(jid)
                continue
            if status in _TERMINAL:
                # finished (aggregate) or canceled/stopped (just drop).
                if status == "finished" and isinstance(job.result, dict):
                    for k, v in job.result.items():
                        if isinstance(v, int):
                            total[k] = total.get(k, 0) + v
                pending.discard(jid)

        done = len(job_ids) - len(pending)
        if done != last_report:
            logger.info(f"[coordinator] run #{run_id}: {done}/{len(job_ids)} chunks done")
            last_report = done

    logger.info(f"[coordinator] run #{run_id} complete: {total}")
    return total


async def run_discovery_via_queue(run_id: int) -> dict:
    """Enqueue + monitor a discovery run. Drop-in replacement for worker.run_once()."""
    job_ids = await enqueue_run(run_id)
    if not job_ids:
        logger.warning(f"[coordinator] run #{run_id}: no active configs / no jobs enqueued")
        return {k: 0 for k in _STAT_KEYS}
    return await monitor_run(run_id, job_ids)
