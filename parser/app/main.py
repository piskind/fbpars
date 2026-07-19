import asyncio
import signal
from datetime import datetime, timezone
from loguru import logger
from app.config import settings


def _redact_dsn(dsn: str) -> str:
    """Hide the password in a DB URL before logging it.
    postgresql+asyncpg://spy:secret@host:5432/db → postgresql+asyncpg://spy:***@host:5432/db"""
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(dsn)
        if parts.password is None:
            return dsn
        userinfo = parts.username or ""
        if parts.password:
            userinfo += ":***"
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        netloc = f"{userinfo}@{host}" if userinfo else host
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        # Never let logging a redacted DSN leak the raw one on a parse error.
        return "<redacted>"


async def _sleep_or_stop(stop: asyncio.Event, secs: float) -> bool:
    """Sleep up to `secs`, returning early with True if shutdown was requested."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=secs)
        return True
    except asyncio.TimeoutError:
        return False


async def _flush_logs(run_id: int, log_lines: list[str]) -> None:
    from app.db import AsyncSessionLocal
    from app.models import ParserRun
    async with AsyncSessionLocal() as session:
        run = await session.get(ParserRun, run_id)
        if run:
            run.log_tail = "\n".join(log_lines[-300:])
            await session.commit()


async def _log_flush_loop(run_id: int, log_lines: list[str], stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(10)
        try:
            await _flush_logs(run_id, log_lines)
        except Exception:
            pass


async def _run_discovery(run_id: int) -> None:
    from app.db import AsyncSessionLocal
    from app.models import ParserRun

    log_lines: list[str] = []

    def _log_sink(message: object) -> None:
        line = str(message).rstrip()
        log_lines.append(line)
        if len(log_lines) > 600:
            log_lines.pop(0)

    from app import coordinator

    sink_id = logger.add(_log_sink, format="{time:HH:mm:ss} | {level} | {message}", colorize=False)
    stop = asyncio.Event()
    flush_task = asyncio.create_task(_log_flush_loop(run_id, log_lines, stop))
    stats = None
    error = None
    status = "done"
    try:
        if settings.use_queue:
            from app.coordinator import run_discovery_via_queue
            stats = await run_discovery_via_queue(run_id)
        else:
            from app.worker import run_once
            stats = await run_once()
    except Exception as exc:
        logger.error(f"[discovery] fatal: {exc}")
        error = str(exc)
        status = "failed"
    else:
        status = "done"
    finally:
        logger.remove(sink_id)
        stop.set()
        await flush_task

    # A SIGTERM/restart mid-run is not a completed run — mark it 'cancelled' (not 'done') so
    # it doesn't block the next /start and resumes from chunk_progress on the next trigger.
    interrupted = coordinator.shutdown_event.is_set()

    async with AsyncSessionLocal() as session:
        run = await session.get(ParserRun, run_id)
        if run:
            if run.status != "cancelled":
                run.status = "cancelled" if interrupted else status
                run.finished_at = datetime.now(timezone.utc)
            if stats is not None:
                run.stats = stats  # authoritative aggregate from the monitor
            elif error is not None:
                # Fatal before an aggregate — keep the incremental stats the monitor already
                # wrote and just annotate the error, rather than blanking the columns.
                run.stats = {**(run.stats or {}), "error": error}
            run.log_tail = "\n".join(log_lines[-300:])
            await session.commit()


async def _discovery_poll_loop(stop: asyncio.Event) -> None:
    from app.db import AsyncSessionLocal
    from app.models import ParserRun
    from sqlalchemy import select

    logger.info("[discovery-poll] started, polling every 15s")
    while not stop.is_set():
        if await _sleep_or_stop(stop, 15):
            break
        try:
            async with AsyncSessionLocal() as session:
                run = (await session.execute(
                    select(ParserRun)
                    .where(ParserRun.status == "triggered")
                    .order_by(ParserRun.triggered_at)
                    .limit(1)
                )).scalar_one_or_none()

                if run:
                    run.status = "running"
                    run.started_at = datetime.now(timezone.utc)
                    await session.commit()
                    run_id = run.id
                else:
                    run_id = None

            if run_id:
                logger.info(f"[discovery-poll] starting run #{run_id}")
                await _run_discovery(run_id)
                logger.info(f"[discovery-poll] run #{run_id} finished")

        except Exception as exc:
            logger.error(f"[discovery-poll] error: {exc}")

    logger.info("[discovery-poll] stopped")


async def main():
    from app import coordinator

    logger.info("Parser starting up")
    logger.info(f"DB: {_redact_dsn(settings.database_url)}")
    logger.info(f"Proxy gateway: {settings.proxy_http_gateway}")
    logger.info(f"S3 bucket: {settings.s3_bucket}")

    import httpx
    try:
        async with httpx.AsyncClient(proxy=settings.proxy_http_gateway, timeout=30) as client:
            resp = await client.get("https://api.ipify.org")
            logger.info(f"Our IP via proxy: {resp.text}")
    except Exception as exc:
        logger.warning(f"IP check failed: {exc}")

    # SIGTERM/SIGINT handling: the coordinator runs as PID 1 in its container, and PID 1
    # gets NO default disposition for unhandled signals — so `docker compose restart parser`
    # used to hang the full 10s stop-grace and then SIGKILL (dropping stats + logs). Register
    # handlers that ask the poll loop + monitor to wind down promptly and persist state.
    stop = asyncio.Event()

    def _request_shutdown(signame: str) -> None:
        logger.info(f"[main] received {signame} — shutting down gracefully")
        coordinator.shutdown_event.set()
        stop.set()

    loop = asyncio.get_running_loop()
    for signame in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, signame), _request_shutdown, signame)
        except (NotImplementedError, AttributeError):
            pass  # e.g. non-Unix; fall back to default behaviour

    logger.info("Starting discovery poll loop")
    await _discovery_poll_loop(stop)
    logger.info("[main] shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
