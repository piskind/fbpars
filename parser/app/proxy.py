import asyncio
import time
import httpx
from loguru import logger
from app.config import settings


async def rotate_ip() -> bool:
    if not settings.proxy_rotate_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(settings.proxy_rotate_url)
            resp.raise_for_status()
            logger.info(f"IP rotation triggered: {resp.text[:100]}")
        await asyncio.sleep(settings.proxy_rotate_wait_sec)
        return True
    except Exception as e:
        logger.warning(f"IP rotation failed: {e}")
        return False


async def current_ip() -> str | None:
    try:
        async with httpx.AsyncClient(proxy=settings.proxy_http_gateway, timeout=15) as client:
            resp = await client.get("https://api.ipify.org")
            return resp.text.strip()
    except Exception as e:
        logger.warning(f"IP check failed: {e}")
        return None


async def rotate_ip_verified() -> bool:
    """Rotate IP and confirm it changed — the rotate endpoint often 200s but keeps the same IP."""
    before = await current_ip()
    if not await rotate_ip():
        logger.warning("rotate_ip_verified: rotation trigger failed")
        return False
    after = await current_ip()

    if before is None or after is None:
        logger.warning(f"rotate_ip_verified: could not read IP (before={before}, after={after})")
        return False
    if before == after:
        logger.warning(f"rotate_ip_verified: IP unchanged ({after}) — proxy stuck")
        return False

    logger.info(f"rotate_ip_verified: IP changed {before} → {after}")
    return True


# ═══════════════════ Phase 3: ProxyProvider abstraction ═══════════════════
# Two implementations switched by settings.proxy_mode:
#   "rotate_url" — single mobile channel via gost; change IP by hitting a rotate URL.
#   "pool"       — residential pool; one IP per request from PROXY_POOL or `proxies` table.
# Both degrade gracefully to the single gost gateway (with a loud warning) when no real
# proxies are configured — so nothing crashes before the pool is bought.

_DEAD_COOLDOWN_SEC = 300  # temp-exclude a pool IP for this long after a rate limit / failure


def _parse_config_pool(entries: list[str]) -> list[str]:
    """Turn PROXY_POOL 'host:port:user:pass' (or 'host:port') strings into proxy URLs."""
    urls: list[str] = []
    for e in entries:
        e = e.strip()
        if not e:
            continue
        parts = e.split(":")
        if len(parts) == 4:
            host, port, user, pw = parts
            urls.append(f"http://{user}:{pw}@{host}:{port}")
        elif len(parts) == 2:
            host, port = parts
            urls.append(f"http://{host}:{port}")
        else:
            logger.warning(f"[proxy] ignoring malformed PROXY_POOL entry: {e!r}")
    return urls


async def _load_pool_from_db() -> list[str]:
    """Build proxy URLs from active rows in the `proxies` table (model already exists)."""
    from sqlalchemy import select
    from app.db import AsyncSessionLocal
    from app.models import Proxy

    try:
        async with AsyncSessionLocal() as session:
            rows = list((await session.execute(
                select(Proxy).where(Proxy.is_active.is_(True))
            )).scalars().all())
    except Exception as exc:
        logger.warning(f"[proxy] could not load pool from DB: {exc}")
        return []

    urls: list[str] = []
    for p in rows:
        auth = f"{p.username}:{p.password}@" if p.username else ""
        urls.append(f"{p.proxy_type or 'http'}://{auth}{p.host}:{p.port}")
    return urls


class ProxyProvider:
    async def get_proxy_url(self) -> str | None:
        raise NotImplementedError

    async def rotate_before_request(self) -> None:
        """Preventive rotation before the next request (mode-dependent)."""
        return None

    async def report_rate_limited(self, proxy_url: str | None = None) -> bool:
        """React to a 1675004 / challenge. Returns True if the next request should use a new IP."""
        return False


class RotateUrlProvider(ProxyProvider):
    """Single mobile channel through gost; IP changes only by hitting the rotate URL."""

    async def get_proxy_url(self) -> str | None:
        return settings.proxy_http_gateway

    async def rotate_before_request(self) -> None:
        # Rotating every request on a single channel costs the full rotate wait — skip it.
        # We rotate reactively in report_rate_limited instead.
        return None

    async def report_rate_limited(self, proxy_url: str | None = None) -> bool:
        return await rotate_ip_verified()


class PoolProvider(ProxyProvider):
    """Residential pool: round-robin one IP per request, temp-exclude dead ones."""

    def __init__(self) -> None:
        self._pool: list[str] = []
        self._loaded = False
        self._idx = 0
        self._dead: dict[str, float] = {}

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._pool = _parse_config_pool(settings.proxy_pool)
        if not self._pool and settings.proxy_pool_from_db:
            self._pool = await _load_pool_from_db()
        self._loaded = True
        if not self._pool:
            logger.warning(
                "PROXY POOL EMPTY — running single IP via gost, will be banned fast. "
                "Fill PROXY_POOL or the `proxies` table when the pool is bought."
            )
        else:
            logger.info(f"[proxy] pool loaded: {len(self._pool)} IP(s)")

    def _alive(self) -> list[str]:
        now = time.time()
        return [p for p in self._pool if self._dead.get(p, 0) < now]

    async def get_proxy_url(self) -> str | None:
        await self._ensure_loaded()
        alive = self._alive()
        if not alive:
            # Empty/all-dead pool → placeholder single IP so we don't crash.
            return settings.proxy_http_gateway
        url = alive[self._idx % len(alive)]
        self._idx += 1
        return url

    async def report_rate_limited(self, proxy_url: str | None = None) -> bool:
        if proxy_url and proxy_url != settings.proxy_http_gateway:
            self._dead[proxy_url] = time.time() + _DEAD_COOLDOWN_SEC
            logger.warning(f"[proxy] IP cooled down {_DEAD_COOLDOWN_SEC}s: {proxy_url.split('@')[-1]}")
            return True
        # Single-IP fallback → nothing better to switch to; try rotate URL if any.
        return await rotate_ip_verified()


_provider: ProxyProvider | None = None


def get_provider() -> ProxyProvider:
    global _provider
    if _provider is None:
        _provider = PoolProvider() if settings.proxy_mode == "pool" else RotateUrlProvider()
        logger.info(f"[proxy] provider = {type(_provider).__name__} (mode={settings.proxy_mode})")
    return _provider


# Module-level convenience wrappers (paginator calls these; provider stays swappable).
async def get_proxy_url() -> str | None:
    return await get_provider().get_proxy_url()


async def rotate_before_request() -> None:
    return await get_provider().rotate_before_request()


async def report_rate_limited(proxy_url: str | None = None) -> bool:
    return await get_provider().report_rate_limited(proxy_url)