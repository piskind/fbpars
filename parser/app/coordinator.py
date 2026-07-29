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
    "skipped_duplicate", "skipped_no_media", "skipped_already_reviewed",
    "skipped_phash_duplicate", "removed_no_media",
)

# RQ terminal statuses (canceled/stopped spellings vary across rq versions).
_TERMINAL = {"finished", "failed", "canceled", "cancelled", "stopped"}

# Set by app.main's SIGTERM/SIGINT handler so monitor_run can bail out promptly (persisting
# stats) instead of leaving the coordinator to be SIGKILLed 10s into a docker restart.
shutdown_event = asyncio.Event()


def _iso_to_date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


_GEO_BY_CONFIG: dict = {}  # config_id -> country, заполняется в enqueue_run для админ-логов

# Media-срезы для параллельного сбора filters-конфигов. Непересекающиеся по типу медиа
# (пересечения дедуплятся upsert'ом по library_id). Покрывают все объявы с медиа; 'none'
# (текст без медиа) не берём — worker всё равно их пропускает (skipped_no_media).
_MEDIA_SEGMENTS = ("image", "video", "meme")


def _chunk_label(config_id, df_iso: str | None, dt_iso: str | None) -> str:
    """Readable '#<cfg> <geo> [df..dt]' for the admin log, from a job's kwargs."""
    geo = _GEO_BY_CONFIG.get(config_id, "")
    tag = (f"#{config_id} {geo}").rstrip()
    if df_iso or dt_iso:
        return f"{tag} [{df_iso}..{dt_iso}]"
    return tag


async def _persist_run_stats(run_id: int, total: dict) -> None:
    """Write the running aggregate to parser_runs.stats NOW, so a cancel / crash / SIGKILL
    keeps whatever finished chunks contributed (Task: stats were only written once at the
    very end, and the coordinator is frequently killed before it gets there)."""
    try:
        async with AsyncSessionLocal() as session:
            run = await session.get(ParserRun, run_id)
            if run:
                run.stats = dict(total)
                await session.commit()
    except Exception as exc:
        logger.warning(f"[coordinator] run #{run_id}: could not persist stats: {exc}")


async def _persist_chunk_result(run_id: int, config_id, df_iso: str | None, dt_iso: str | None, result: dict) -> None:
    """Store a finished chunk's final stats in chunk_progress (last_stats/last_run_id) so the
    per-chunk 'done' totals survive a docker image rebuild — logs get wiped, the DB doesn't.
    UPDATE-only: a chunk that saved nothing has no row and needs none (it collected nothing)."""
    if config_id is None:
        return
    try:
        kdf, kdt = chunk_key(_iso_to_date(df_iso), _iso_to_date(dt_iso))
        async with AsyncSessionLocal() as session:
            row = await session.get(ChunkProgress, (config_id, kdf, kdt))
            if row is not None:
                row.last_stats = {k: v for k, v in result.items() if isinstance(v, int)}
                row.last_run_id = run_id
                await session.commit()
    except Exception as exc:
        logger.warning(f"[coordinator] could not persist chunk result for #{config_id}: {exc}")


async def _log_chunk_progress(coords: list, progress_seen: dict, started_seen: set) -> None:
    """Surface live collection into the admin log_tail by reading chunk_progress (the workers
    run in separate processes, so their own stdout never reaches the run's log sink). Logs a
    'collecting' line the first time a chunk appears and a '+N cards' delta as it grows."""
    if not coords:
        return
    try:
        async with AsyncSessionLocal() as session:
            for config_id, kdf, kdt, label in coords:
                row = await session.get(ChunkProgress, (config_id, kdf, kdt))
                if row is None:
                    continue
                key = (config_id, kdf, kdt)
                prev = progress_seen.get(key)
                if key not in started_seen:
                    started_seen.add(key)
                    logger.info(
                        f"[coordinator] chunk {label} collecting: "
                        f"{row.collected_count} saved so far (has_next={row.has_next})"
                    )
                elif prev is not None and row.collected_count != prev:
                    logger.info(
                        f"[coordinator] chunk {label}: committed batch +{row.collected_count - prev} "
                        f"cards (total {row.collected_count}, has_next={row.has_next})"
                    )
                progress_seen[key] = row.collected_count
    except Exception as exc:
        logger.warning(f"[coordinator] chunk-progress log failed: {exc}")


def _chunks_for(config) -> list[tuple[date | None, date | None]]:
    """Mirror worker.process_config's date-splitting decision, but down to chunk_days."""
    effective_date_from = min(config.date_from or date(2019, 1, 1), date(2019, 1, 1))
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


async def enqueue_run(run_id: int, mode_override: str | None = None) -> list[str]:
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
        run = await session.get(ParserRun, run_id)
        # mode_override — для «подхватить <тип>» (reload); иначе берём режим самого рана.
        mode = mode_override or (getattr(run, "mode", None) if run else None)  # keyword / filters / all
        configs = await get_active_configs(session, mode=mode)
        for _c in configs:
            _GEO_BY_CONFIG[_c.id] = (getattr(_c, 'country', None) or '?')

        for config in configs:
            # ── Media-сегментация для filters/fanpage ──
            # Вместо лоссовых узких date-окон (start_date[min] недобирает в разы) гоним
            # НЕСКОЛЬКО ШИРОКИХ срезов по типу медиа (image/video/meme) — каждый на своём
            # воркере ПАРАЛЛЕЛЬНО, весь диапазон дат ([max]=date_to, без [min]). upsert по
            # library_id дедуплит пересечения. Даёт и параллелизм, и полноту. Резюма нет
            # (track_chunk=False на media-джобах) — прерывание безопасно (upsert идемпотентен).
            if config.config_type in ("filters", "fanpage"):
                df = min(config.date_from or date(2019, 1, 1), date(2019, 1, 1))
                dt = config.date_to
                for mt in _MEDIA_SEGMENTS:
                    jid = f"chunk_{config.id}_{mt}_{run_id}"
                    if Job.exists(jid, connection=conn):
                        continue
                    q.enqueue(
                        parse_chunk,
                        kwargs={
                            "config_id": config.id,
                            "date_from": _iso(df),
                            "date_to": _iso(dt),
                            "media_type": mt,
                            "run_id": run_id,
                        },
                        job_id=jid,
                        job_timeout=settings.parse_job_timeout,
                        result_ttl=3600,
                        failure_ttl=86400,
                        retry=Retry(max=3, interval=[60, 180, 300]),
                    )
                    job_ids.append(jid)
                continue
            # ── keyword и прочие: обычный date-chunk путь (одно окно, резюмируемое) ──
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
                # Colon-free so it can't collide with RQ's `rq:job:<id>` Redis key namespace
                # (baked in here so no post-pull sed is needed — that sed also ate the colon
                # in `track_chunk:` annotations and broke worker.py).
                jid = f"chunk_{config.id}_{df}_{dt}_{run_id}"
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
    # Chunk-outcome counters kept SEPARATE from card-level `errors`: a failed chunk is one
    # unit, not one "error", so "7/21 done" can no longer secretly mean "7 died".
    total["chunks_done"] = 0    # jobs that finished successfully
    total["chunks_failed"] = 0  # jobs that failed terminally / vanished
    pending = set(job_ids)
    last_report = (-1, -1)      # (done, failed) — re-log when either moves
    progress_seen: dict = {}   # chunk key -> collected_count last logged (delta detection)
    started_seen: set = set()  # chunks we've already logged a first "collecting" line for
    polls = 0

    while pending:
        await asyncio.sleep(poll_sec)
        polls += 1

        # Graceful shutdown (SIGTERM): persist what we have and stop monitoring. Jobs keep
        # running in the workers and resume from chunk_progress on the next run.
        if shutdown_event.is_set():
            await _persist_run_stats(run_id, total)
            logger.info(f"[coordinator] run #{run_id}: shutdown requested — stats persisted, monitor stopping")
            return total

        # A single bad poll (Redis hiccup, transient fetch error) must not silently kill the
        # monitor loop — that's the "0/33 chunks done then silence for hours" symptom.
        try:
            # Honour admin cancel: stop pending jobs and bail (keeping stats).
            async with AsyncSessionLocal() as session:
                run = await session.get(ParserRun, run_id)
            if run and run.status == "cancelled":
                for jid in list(pending):
                    try:
                        Job.fetch(jid, connection=conn).cancel()
                    except Exception:
                        pass
                await _persist_run_stats(run_id, total)
                logger.info(f"[coordinator] run #{run_id} cancelled — stopped {len(pending)} pending job(s)")
                return total

            # «Подхватить <тип>»: админка выставила reload_mode ('keyword'|'filters'|'all') →
            # до-enqueue'им новые активные конфиги ЭТОГО типа на лету (можно и тип, отличный от
            # режима рана — так «подхватить фильтры» расширит keyword-ран). enqueue_run сам
            # пропустит уже стоящие/исчерпанные чанки, вернёт только реально новые джобы.
            reload_mode = getattr(run, "reload_mode", None) if run else None
            if reload_mode:
                try:
                    new_ids = await enqueue_run(run_id, mode_override=reload_mode)
                except Exception as exc:
                    new_ids = []
                    logger.warning(f"[coordinator] run #{run_id}: reload enqueue failed: {exc}")
                if new_ids:
                    pending.update(new_ids)
                async with AsyncSessionLocal() as session:
                    r2 = await session.get(ParserRun, run_id)
                    if r2:
                        r2.reload_mode = None
                        await session.commit()
                logger.info(
                    f"[coordinator] run #{run_id}: подхвачено {len(new_ids)} новых чанк-джоб "
                    f"(reload тип={reload_mode})"
                )

            # Move jobs whose work-horse died (OOM / timeout / crash) out of 'started' so we
            # don't wait on them forever; then treat FailedJobRegistry membership as terminal.
            try:
                started_reg.cleanup()
                failed_reg.cleanup()
                failed_ids = set(failed_reg.get_job_ids())
            except Exception as exc:
                logger.warning(f"[coordinator] registry cleanup failed: {exc}")
                failed_ids = set()

            # Fetch each pending job once; reuse for both status and progress logging.
            jobs: dict = {}
            for jid in list(pending):
                try:
                    jobs[jid] = Job.fetch(jid, connection=conn)
                except Exception:
                    # Job data expired/gone — stop waiting, count it as a failed chunk.
                    logger.warning(f"[coordinator] job {jid} vanished — treating as failed")
                    total["chunks_failed"] += 1
                    pending.discard(jid)

            retrying = 0  # jobs waiting on a scheduled Retry (not yet terminal, not stuck)
            for jid, job in jobs.items():
                if jid not in pending:
                    continue
                kw = job.kwargs or {}
                label = _chunk_label(kw.get("config_id"), kw.get("date_from"), kw.get("date_to"))
                status = job.get_status(refresh=True)
                if jid in failed_ids or status == "failed":
                    total["chunks_failed"] += 1
                    tail = (job.exc_info or "")[-300:]
                    logger.warning(f"[coordinator] chunk {label} FAILED: {tail}")
                    pending.discard(jid)
                    continue
                if status in _TERMINAL:
                    # finished (aggregate + log a real summary) or canceled/stopped (just drop).
                    if status == "finished" and isinstance(job.result, dict):
                        r = job.result
                        for k, v in r.items():
                            if isinstance(v, int):
                                total[k] = total.get(k, 0) + v
                        total["chunks_done"] += 1
                        if r.get("skipped_zombie"):
                            # A no-op skip (exhausted day / stale run) — don't clobber the
                            # chunk's real last_stats with a placeholder.
                            logger.info(f"[coordinator] chunk {label} skipped (zombie/exhausted)")
                        else:
                            logger.info(
                                f"[coordinator] chunk {label} done: raw={r.get('raw', 0)} "
                                f"new={r.get('new', 0)} updated={r.get('updated', 0)} "
                                f"skipped_reviewed={r.get('skipped_already_reviewed', 0)} "
                                f"errors={r.get('errors', 0)}"
                            )
                            # Persist the per-chunk totals to the DB (survives log wipes).
                            await _persist_chunk_result(
                                run_id, kw.get("config_id"), kw.get("date_from"), kw.get("date_to"), r
                            )
                    pending.discard(jid)
                elif status in ("scheduled", "deferred"):
                    retrying += 1  # a failed attempt is queued to retry with backoff

            # Surface live collection (committed batches, chunk starts) from chunk_progress.
            coords = []
            for jid in pending:
                job = jobs.get(jid)
                if job is None:
                    continue
                kw = job.kwargs or {}
                cid = kw.get("config_id")
                if cid is None:
                    continue
                kdf, kdt = chunk_key(_iso_to_date(kw.get("date_from")), _iso_to_date(kw.get("date_to")))
                coords.append((cid, kdf, kdt, _chunk_label(cid, kw.get("date_from"), kw.get("date_to"))))
            await _log_chunk_progress(coords, progress_seen, started_seen)

            # Persist the aggregate incrementally so cancel/crash/kill keeps the numbers.
            await _persist_run_stats(run_id, total)

            done = total["chunks_done"]
            failed = total["chunks_failed"]
            if (done, failed) != last_report:
                logger.info(
                    f"[coordinator] run #{run_id}: {done} done, {failed} failed, "
                    f"{retrying} retrying, {len(pending)} pending / {len(job_ids)} chunks"
                )
                last_report = (done, failed)
            elif polls % 12 == 0:
                # Heartbeat (~once a minute at poll_sec=5) so the monitor is never silent for
                # hours — makes a genuine stall visible instead of looking like a dead loop.
                logger.info(
                    f"[coordinator] run #{run_id}: {done} done, {failed} failed, {retrying} retrying, "
                    f"{len(pending)} pending (new={total['new']} updated={total['updated']} raw={total['raw']})"
                )
        except Exception as exc:
            logger.warning(f"[coordinator] run #{run_id}: monitor poll error (continuing): {exc}")

    await _persist_run_stats(run_id, total)
    logger.info(f"[coordinator] run #{run_id} complete: {total}")
    return total


async def run_discovery_via_queue(run_id: int) -> dict:
    """Enqueue + monitor a discovery run. Drop-in replacement for worker.run_once()."""
    job_ids = await enqueue_run(run_id)
    if not job_ids:
        logger.warning(f"[coordinator] run #{run_id}: no active configs / no jobs enqueued")
        return {k: 0 for k in _STAT_KEYS}
    return await monitor_run(run_id, job_ids)
