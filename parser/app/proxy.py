import asyncio
import hashlib
import random
import socket
import os
import time
import httpx
from loguru import logger
from app.config import settings


def worker_gateway() -> str:
    """The upstream gost channel THIS worker should route through.

    With several channels configured (settings.proxy_http_gateways) each worker binds to ONE,
    chosen by a stable hash of its container hostname, so N workers spread across the exit-IPs
    instead of all sharing one. With none configured, returns the single PROXY_HTTP_GATEWAY —
    current behaviour, unchanged. Stable across restarts (hashlib, not the salted builtin hash)."""
    gateways = settings.proxy_http_gateways
    if not gateways:
        return settings.proxy_http_gateway
    if len(gateways) == 1:
        return gateways[0]
    digest = hashlib.md5(socket.gethostname().encode()).hexdigest()
    return gateways[int(digest, 16) % len(gateways)]


async def rotate_ip() -> bool:
    if not settings.proxy_rotate_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(settings.proxy_rotate_url)
            # fxdx answers 429 when workers hammer the rotate endpoint together — the
            # rotation does NOT happen, so back off instead of treating it as success.
            if resp.status_code == 429:
                logger.warning(
                    f"IP rotation 429 (too many requests) — backing off "
                    f"{settings.rotate_429_backoff_sec}s"
                )
                await asyncio.sleep(settings.rotate_429_backoff_sec)
                return False
            resp.raise_for_status()
            logger.info(f"IP rotation triggered: {resp.text[:100]}")
        await asyncio.sleep(settings.proxy_rotate_wait_sec)
        return True
    except Exception as e:
        logger.warning(f"IP rotation failed: {e}")
        return False


async def current_ip() -> str | None:
    try:
        async with httpx.AsyncClient(proxy=worker_gateway(), timeout=15) as client:
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


# ─────────────────── rotation storm control (Redis lock) ───────────────────
# One shared exit IP for all workers → rotating it must be serialised. Without this,
# concurrent rate limits fan out into a burst of rotate calls that fxdx answers with 429,
# so the IP never actually flips and the rate limit never clears (the "rotation storm").
_ROTATE_LOCK_KEY = "parser:rotate:lock"
_ROTATE_COOLDOWN_KEY = "parser:rotate:cooldown"


def _rotate_redis():
    """Shared Redis (same one RQ uses); None if unavailable so we degrade to plain rotation."""
    try:
        from app.queue import get_redis
        return get_redis()
    except Exception:
        return None


async def rotate_ip_guarded() -> bool:
    """Rotate the single shared exit IP under a Redis lock + global cooldown.

    Only one worker actually hits the rotate URL at a time; the others wait out the settle
    pause and reuse the freshly-rotated shared IP instead of piling on (that pile-on is
    what makes fxdx return 429). A global cooldown caps how often the IP flips. Falls back
    to a plain verified rotation when Redis is unavailable (legacy behaviour)."""
    r = _rotate_redis()
    if r is None:
        return await rotate_ip_verified()

    # Someone rotated within the cooldown window → the shared IP already changed; settle & reuse.
    try:
        if r.exists(_ROTATE_COOLDOWN_KEY):
            await asyncio.sleep(settings.rotate_settle_sec)
            return True
    except Exception:
        pass

    # Exclusivity: only the lock holder calls the rotate endpoint.
    try:
        got = bool(r.set(_ROTATE_LOCK_KEY, "1", nx=True, ex=settings.rotate_lock_ttl_sec))
    except Exception:
        got = False

    if not got:
        # Another worker is rotating the shared channel right now — wait for it, don't storm.
        await asyncio.sleep(settings.rotate_settle_sec)
        return True

    try:
        ok = await rotate_ip_verified()
        if ok:
            try:
                r.set(_ROTATE_COOLDOWN_KEY, "1", ex=settings.rotate_cooldown_sec)
            except Exception:
                pass
            # Let the new upstream IP come up before the next FB hit (avoids the 503 seen
            # immediately after a change).
            await asyncio.sleep(settings.rotate_settle_sec)
        return ok
    finally:
        try:
            r.delete(_ROTATE_LOCK_KEY)
        except Exception:
            pass


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


_STICKY_REQUESTS = int(os.getenv("PROXY_STICKY_REQUESTS", "15") or 1)


class ProxyProvider:
    async def get_proxy_url(self, force_pool: bool = False) -> str | None:
        """force_pool=True — выдать адрес ИЗ ПУЛА, минуя мобильный канал.

        Нужно для перепроверки пустых ответов: ротация мобильного IP режется общим
        кулдауном, и три попытки подряд уходили через тот же придушенный выход —
        так терялись целые ключи (Hammer of Thor/EG, Big Hunter/IN дали ноль
        в прогоне и по 10 карточек на ручной перепробе).
        """
        raise NotImplementedError

    async def rotate_before_request(self) -> None:
        """Preventive rotation before the next request (mode-dependent)."""
        return None

    async def report_rate_limited(self, proxy_url: str | None = None) -> bool:
        """React to a 1675004 / challenge. Returns True if the next request should use a new IP."""
        return False

    def report_transport_error(self, proxy_url: str | None = None) -> None:
        """Транспортный сбой (канал не отвечает). Провайдер решает, отключать ли полосу."""
        return None

    def report_success(self, proxy_url: str | None = None) -> None:
        """Успешный ответ по каналу — снимает подозрение на его смерть."""
        return None


class RotateUrlProvider(ProxyProvider):
    """Single mobile channel through gost; IP changes only by hitting the rotate URL."""

    async def get_proxy_url(self, force_pool: bool = False) -> str | None:
        # Канал здесь один, уходить некуда — force_pool не на что применить.
        return worker_gateway()

    async def rotate_before_request(self) -> None:
        # Rotating every request on a single channel costs the full rotate wait — skip it.
        # We rotate reactively in report_rate_limited instead.
        return None

    async def report_rate_limited(self, proxy_url: str | None = None) -> bool:
        return await rotate_ip_guarded()


class PoolProvider(ProxyProvider):
    """Residential pool: round-robin one IP per request, temp-exclude dead ones."""

    def __init__(self) -> None:
        self._pool: list[str] = []
        self._loaded = False
        self._idx = 0
        self._dead: dict[str, float] = {}
        # Ротация на КАЖДЫЙ запрос = новое TCP+TLS подключение к шлюзу каждый раз, а режет
        # нас именно число подключений. Держим один порт несколько запросов подряд.
        self._sticky_left = 0
        self._current: str | None = None

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

    async def get_proxy_url(self, force_pool: bool = False) -> str | None:
        # Это и есть пул: force_pool здесь уже выполнено по построению.
        await self._ensure_loaded()
        alive = self._alive()
        if not alive:
            # Empty/all-dead pool → placeholder single channel so we don't crash.
            return worker_gateway()
        # force_pool: рвём липкость. Этот флаг ставят только при перепроверке пустого
        # ответа, а там нужен ДРУГОЙ выход, а не тот же самый ещё раз. Без этого
        # «смена канала» возвращала прежний адрес: пул держит порт _STICKY_REQUESTS
        # запросов подряд, и все попытки уходили через один придушенный IP.
        if force_pool:
            self._sticky_left = 0
        # переиспользуем текущий порт, пока не истёк лимит липкости
        elif self._current is not None and self._sticky_left > 0 and self._current in alive:
            self._sticky_left -= 1
            return self._current
        url = alive[self._idx % len(alive)]
        self._idx += 1
        self._current = url
        self._sticky_left = max(0, _STICKY_REQUESTS - 1)
        return url

    async def report_rate_limited(self, proxy_url: str | None = None) -> bool:
        if proxy_url and proxy_url != settings.proxy_http_gateway:
            self._dead[proxy_url] = time.time() + _DEAD_COOLDOWN_SEC
            self._sticky_left = 0  # лимит от FB — уходим с этого порта немедленно
            logger.warning(f"[proxy] IP cooled down {_DEAD_COOLDOWN_SEC}s: {proxy_url.split('@')[-1]}")
            return True
        # Single-IP fallback → nothing better to switch to; try rotate URL if any.
        return await rotate_ip_guarded()

    def report_transport_error(self, proxy_url: str | None = None) -> None:
        # Обрыв соединения — порт мёртв прямо сейчас. Гасим и рвём sticky, иначе
        # тот же дохлый порт бьётся до 15 раз подряд с секундной паузой между попытками.
        if proxy_url and proxy_url != settings.proxy_http_gateway:
            self._dead[proxy_url] = time.time() + _DEAD_COOLDOWN_SEC
            self._sticky_left = 0
            logger.warning(f"[proxy] IP dead (transport error), cooled down {_DEAD_COOLDOWN_SEC}s: {proxy_url.split('@')[-1]}")



class HybridProvider(ProxyProvider):
    """Мобильный gost как основная полоса + резидентский пул как вторая.

    Доля gost задаётся HYBRID_GOST_SHARE (0..1). Пул при этом остаётся под своим лимитом
    потоков, а общий параллелизм растёт за счёт мобильного канала.
    """

    def __init__(self) -> None:
        self._pool = PoolProvider()
        self._share = float(os.getenv("HYBRID_GOST_SHARE", "0.75") or 0.75)
        # Выключатель полосы: столько подряд транспортных сбоев — и канал считается мёртвым.
        self._fail_limit = int(os.getenv("LANE_FAIL_LIMIT", "10") or 10)
        self._dead_sec = int(os.getenv("LANE_DEAD_SEC", "300") or 300)
        self._gost_fails = 0
        self._gost_dead_until = 0.0
        # Профилактическая смена IP: FB душит мобильный exit пустыми ответами,
        # ротация возвращает полную выдачу. 0 — выключить.
        self._rotate_every = int(os.getenv("ROTATE_EVERY_SEC", "180") or 0)
        self._last_rotate = time.time()
        logger.info(f"[proxy] hybrid: доля мобильного канала {self._share:.0%}")

    async def rotate_before_request(self) -> None:
        # Раз в _rotate_every секунд меняем мобильный IP, не дожидаясь, пока FB начнёт
        # отдавать пустые ответы. rotate_ip_guarded сам держит лок и глобальный кулдаун,
        # поэтому 32 воркера не превратят это в шторм запросов к fxdx.
        if not self._rotate_every:
            return
        if time.time() - self._last_rotate < self._rotate_every:
            return
        self._last_rotate = time.time()
        try:
            await rotate_ip_guarded()
        except Exception as exc:
            logger.warning(f"[proxy] профилактическая ротация не удалась: {exc}")

    async def get_proxy_url(self, force_pool: bool = False) -> str | None:
        # Полоса помечена мёртвой — не тратим на неё запросы, пока не истечёт срок.
        if force_pool or time.time() < self._gost_dead_until:
            # Флаг обязан дойти до пула: именно он рвёт липкость и выдаёт ДРУГОЙ
            # адрес. Без проброса «смена канала» возвращала тот же порт 15 раз подряд,
            # и перепроверка пустого ответа была бесполезной.
            return await self._pool.get_proxy_url(force_pool=force_pool)
        if random.random() < self._share:
            return worker_gateway()
        return await self._pool.get_proxy_url()

    async def report_rate_limited(self, proxy_url: str | None = None) -> bool:
        # Лимит на мобильном канале лечится сменой IP (под общим локом, чтобы 30 воркеров
        # не дёрнули ротацию одновременно); лимит на пуловом IP — остыванием этого IP.
        if proxy_url and proxy_url == worker_gateway():
            return await rotate_ip_guarded()
        return await self._pool.report_rate_limited(proxy_url)

    def report_transport_error(self, proxy_url: str | None = None) -> None:
        if not proxy_url:
            return
        if proxy_url != worker_gateway():
            # Пуловый IP молчит — гасим немедленно, не дожидаясь исчерпания sticky-окна.
            self._pool.report_transport_error(proxy_url)
            return
        # Считаем подряд идущие сбои ТОЛЬКО мобильного канала: пуловые IP дохнут поштучно
        # и это норма, а мобильный канал один — его отказ означает, что полосы нет.
        self._gost_fails += 1
        if self._gost_fails >= self._fail_limit and time.time() >= self._gost_dead_until:
            self._gost_dead_until = time.time() + self._dead_sec
            logger.error(
                f"[proxy] мобильный канал не отвечает {self._gost_fails} запросов подряд — "
                f"увожу весь трафик в резидентский пул на {self._dead_sec}s"
            )

    def report_success(self, proxy_url: str | None = None) -> None:
        if proxy_url and proxy_url == worker_gateway():
            if self._gost_fails:
                self._gost_fails = 0
            if self._gost_dead_until:
                logger.info("[proxy] мобильный канал снова отвечает — возвращаю его в работу")
                self._gost_dead_until = 0.0

_provider: ProxyProvider | None = None


def get_provider() -> ProxyProvider:
    global _provider
    if _provider is None:
        if settings.proxy_mode == "hybrid":
            _provider = HybridProvider()
        elif settings.proxy_mode == "pool":
            _provider = PoolProvider()
        else:
            _provider = RotateUrlProvider()
        logger.info(f"[proxy] provider = {type(_provider).__name__} (mode={settings.proxy_mode})")
    return _provider


# Module-level convenience wrappers (paginator calls these; provider stays swappable).
async def get_proxy_url(force_pool: bool = False) -> str | None:
    return await get_provider().get_proxy_url(force_pool=force_pool)


async def rotate_before_request() -> None:
    return await get_provider().rotate_before_request()


async def report_rate_limited(proxy_url: str | None = None) -> bool:
    return await get_provider().report_rate_limited(proxy_url)


def report_transport_error(proxy_url: str | None = None) -> None:
    get_provider().report_transport_error(proxy_url)


def report_success(proxy_url: str | None = None) -> None:
    get_provider().report_success(proxy_url)