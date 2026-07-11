"""Redis + RQ wiring (Phase 2).

Two queues:
  - "parse" : one job per (config, date-chunk) — browser-less pagination + upsert.
  - "media" : one job per ad's media set (Phase 4; created here so both workers share config).

Connections are lazy so the module imports cleanly where redis/rq aren't installed.
"""
from functools import lru_cache

from app.config import settings

PARSE_QUEUE = "parse"
MEDIA_QUEUE = "media"


@lru_cache(maxsize=1)
def get_redis():
    from redis import Redis
    return Redis.from_url(settings.redis_url)


def parse_queue():
    from rq import Queue
    return Queue(PARSE_QUEUE, connection=get_redis(), default_timeout=settings.parse_job_timeout)


def media_queue():
    from rq import Queue
    return Queue(MEDIA_QUEUE, connection=get_redis(), default_timeout=settings.media_job_timeout)
