import asyncio
import datetime
from loguru import logger

from app.refresh_worker import run_refresh_once

REFRESH_HOUR = 3
REFRESH_MINUTE = 0
PARSER_HOUR = 4
PARSER_MINUTE = 0


async def _trigger_parser_run() -> None:
    from app.db import AsyncSessionLocal
    from app.models import ParserRun
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        # Наследуем режим последнего рана, чтобы daily-триггер не сбрасывал выбор
        # пользователя (напр. keyword) обратно на 'all'. Дефолт — keyword.
        last_mode = (await session.execute(
            select(ParserRun.mode).order_by(ParserRun.id.desc()).limit(1)
        )).scalar()
        run = ParserRun(status="triggered", mode=last_mode or "keyword")
        session.add(run)
        await session.commit()
        logger.info(f"[scheduler] Created ParserRun #{run.id} (triggered, mode={run.mode})")


def _next_daily(hour: int, minute: int) -> datetime.datetime:
    now = datetime.datetime.now()
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= t:
        t += datetime.timedelta(days=1)
    return t


async def _refresh_loop() -> None:
    while True:
        next_run = _next_daily(REFRESH_HOUR, REFRESH_MINUTE)
        sleep_secs = (next_run - datetime.datetime.now()).total_seconds()
        logger.info(
            f"[scheduler] Next refresh at {next_run.strftime('%Y-%m-%d %H:%M')} "
            f"(in {sleep_secs / 3600:.1f}h)"
        )
        await asyncio.sleep(sleep_secs)
        logger.info("[scheduler] Starting daily refresh")
        await run_refresh_once()


async def _parser_loop() -> None:
    while True:
        next_run = _next_daily(PARSER_HOUR, PARSER_MINUTE)
        sleep_secs = (next_run - datetime.datetime.now()).total_seconds()
        logger.info(
            f"[scheduler] Next parser trigger at {next_run.strftime('%Y-%m-%d %H:%M')} "
            f"(in {sleep_secs / 3600:.1f}h)"
        )
        await asyncio.sleep(sleep_secs)
        logger.info("[scheduler] Triggering daily parser run")
        try:
            await _trigger_parser_run()
        except Exception as exc:
            logger.error(f"[scheduler] Failed to create ParserRun: {exc}")


async def main():
    await asyncio.gather(_refresh_loop(), _parser_loop())


if __name__ == "__main__":
    asyncio.run(main())
