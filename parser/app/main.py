import asyncio
from datetime import datetime, timezone
from loguru import logger
from app.config import settings


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

    sink_id = logger.add(_log_sink, format="{time:HH:mm:ss} | {level} | {message}", colorize=False)
    stop = asyncio.Event()
    flush_task = asyncio.create_task(_log_flush_loop(run_id, log_lines, stop))
    try:
        if settings.use_queue:
            from app.coordinator import run_discovery_via_queue
            stats = await run_discovery_via_queue(run_id)
        else:
            from app.worker import run_once
            stats = await run_once()
    except Exception as exc:
        logger.error(f"[discovery] fatal: {exc}")
        stats = {"error": str(exc)}
        status = "failed"
    else:
        status = "done"
    finally:
        logger.remove(sink_id)
        stop.set()
        await flush_task

    async with AsyncSessionLocal() as session:
        run = await session.get(ParserRun, run_id)
        if run:
            if run.status != "cancelled":
                run.status = status
                run.finished_at = datetime.now(timezone.utc)
            run.stats = stats
            run.log_tail = "\n".join(log_lines[-300:])
            await session.commit()


async def _discovery_poll_loop() -> None:
    from app.db import AsyncSessionLocal
    from app.models import ParserRun
    from sqlalchemy import select

    logger.info("[discovery-poll] started, polling every 15s")
    while True:
        await asyncio.sleep(15)
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


async def main():
    logger.info("Parser starting up")
    logger.info(f"DB: {settings.database_url}")
    logger.info(f"Proxy gateway: {settings.proxy_http_gateway}")
    logger.info(f"S3 bucket: {settings.s3_bucket}")

    import httpx
    try:
        async with httpx.AsyncClient(proxy=settings.proxy_http_gateway, timeout=30) as client:
            resp = await client.get("https://api.ipify.org")
            logger.info(f"Our IP via proxy: {resp.text}")
    except Exception as exc:
        logger.warning(f"IP check failed: {exc}")

    logger.info("Starting discovery poll loop")
    await _discovery_poll_loop()


if __name__ == "__main__":
    asyncio.run(main())
