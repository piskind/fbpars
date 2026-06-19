import asyncio
import datetime
from loguru import logger

from app.refresh_worker import run_refresh_once

RUN_HOUR = 3
RUN_MINUTE = 0


async def main():
    while True:
        now = datetime.datetime.now()
        next_run = now.replace(hour=RUN_HOUR, minute=RUN_MINUTE, second=0, microsecond=0)
        if now >= next_run:
            next_run += datetime.timedelta(days=1)
        sleep_secs = (next_run - now).total_seconds()
        logger.info(
            f"[scheduler] Next refresh at {next_run.strftime('%Y-%m-%d %H:%M')} "
            f"(in {sleep_secs / 3600:.1f}h)"
        )
        await asyncio.sleep(sleep_secs)
        logger.info("[scheduler] Starting daily refresh")
        await run_refresh_once()


if __name__ == "__main__":
    asyncio.run(main())
