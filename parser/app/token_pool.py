"""Общий (на все воркеры) пул токен-сессий FB — вместо запуска браузера на каждый срез.

Замер 20.08 по логам 16 воркеров: срез живёт ~190 с, из них **~50 с** уходит на Chromium
ради lsd/cookies/doc_id, и в 93% случаев первый прокси-канал ещё и отваливался, добавляя
~20 с холостого ожидания. При этом сами токены к запросу никак не привязаны.

Проверено пробой (app/probe_reuse.py): ОДНА сессия обслуживает любой ключ, любое гео и
любой active_status — всё, что определяет выдачу, лежит в GraphQL-переменных
(queryString / countries / activeStatus / sortData / startDate), а не в токенах. Поэтому
сессии складываются в Redis, живут TOKEN_POOL_TTL_SEC и переиспользуются всеми воркерами.

Инвалидация: при устойчивом rate-limit на конкретной сессии воркер зовёт invalidate(),
слот чистится, следующий обратившийся захватывает свежую сессию под Redis-локом (чтобы
16 воркеров не запустили 16 браузеров одновременно).
"""
import asyncio
import json
import os
import random
import time

from loguru import logger

from app.browser import SessionTokens, build_library_url, capture_session_tokens

# Сколько РАЗНЫХ сессий держим одновременно. Одна сессия на весь парк — соблазнительно
# (максимум переиспользования), но тогда все запросы идут под одним lsd, и один бан
# останавливает всех. Несколько слотов размазывают риск и дают запас на инвалидацию.
POOL_SIZE = int(os.getenv("TOKEN_POOL_SIZE", "6") or 6)
# Возраст, после которого слот перезахватывается. FB не публикует срок жизни lsd;
# 15 минут — консервативная оценка, снизу подпирается инвалидацией по rate-limit.
TTL_SEC = int(os.getenv("TOKEN_POOL_TTL_SEC", "900") or 900)
# Потолок ожидания, пока чужой воркер захватывает сессию в этот слот.
LOCK_WAIT_SEC = int(os.getenv("TOKEN_POOL_LOCK_WAIT_SEC", "75") or 75)
ENABLED = (os.getenv("TOKEN_POOL_ENABLED", "true") or "true").lower() not in ("0", "false", "no")

_KEY = "parser:tokens:v1:{}"
_LOCK = "parser:tokens:lock:v1:{}"

# URL для захвата: широкий и заведомо непустой. Узкий срез (редкий ключ + узкая дата) может
# вернуть 0 объяв — тогда FB не шлёт AdLibrarySearchPaginationQuery и токенов не будет.
_CAPTURE_URL = build_library_url(
    country="US", keyword=None, languages=None,
    active_status="all", media_type="all",
    sort_mode="relevancy_monthly_grouped", sort_direction="desc",
)

# Процесс-локальный кеш: внутри одного среза не ходим в Redis на каждое обновление сессии.
_local: dict[int, tuple[SessionTokens, float]] = {}


def _redis():
    try:
        from app.queue import get_redis
        return get_redis()
    except Exception as exc:
        logger.warning(f"[tokens] Redis недоступен ({exc}) — работаем без общего пула")
        return None


def _dump(t: SessionTokens) -> str:
    return json.dumps({
        "cookies": t.cookies,
        "lsd": t.lsd,
        "doc_id": t.doc_id,
        "base_form_data": t.base_form_data,
        "variables_template": t.variables_template,
        "captured_at": t.captured_at,
    }, ensure_ascii=False)


def _load(raw: str | bytes) -> SessionTokens:
    d = json.loads(raw)
    t = SessionTokens(
        cookies=d.get("cookies") or "",
        lsd=d.get("lsd"),
        doc_id=d.get("doc_id"),
        base_form_data=d.get("base_form_data") or {},
        variables_template=d.get("variables_template") or {},
        captured_at=d.get("captured_at") or 0.0,
    )
    return t


def _fresh(t: SessionTokens) -> bool:
    return bool(t.lsd and t.base_form_data) and (time.time() - (t.captured_at or 0)) < TTL_SEC


async def _capture_into(r, slot: int) -> SessionTokens:
    """Захватить сессию браузером и положить в слот. Зовётся под локом."""
    tokens = await capture_session_tokens(_CAPTURE_URL)
    if getattr(tokens, "is_empty", False) or not tokens.base_form_data:
        raise RuntimeError("[tokens] пул: захват вернул пустую сессию")
    try:
        r.set(_KEY.format(slot), _dump(tokens), ex=TTL_SEC + 120)
    except Exception as exc:
        logger.warning(f"[tokens] пул: не смог сохранить слот {slot}: {exc}")
    tokens.pool_slot = slot
    _local[slot] = (tokens, time.time())
    logger.info(f"[tokens] пул: слот {slot} обновлён (doc_id={tokens.doc_id})")
    return tokens


async def get_tokens(url: str, cookies: list | None = None) -> SessionTokens:
    """Готовая сессия из общего пула; захват браузером — только если слот протух.

    url нужен лишь как запасной путь (пул выключен / Redis лёг) — тогда ведём себя как
    раньше и захватываем токены на самом URL среза.
    """
    if not ENABLED:
        return await capture_session_tokens(url, cookies)

    r = _redis()
    if r is None:
        return await capture_session_tokens(url, cookies)

    slots = list(range(POOL_SIZE))
    random.shuffle(slots)  # разводим воркеров по слотам, чтобы не толпились в первом

    # 1) готовая сессия: сперва процесс-локально, потом из Redis
    for slot in slots:
        cached = _local.get(slot)
        if cached and _fresh(cached[0]):
            return cached[0]
        try:
            raw = r.get(_KEY.format(slot))
        except Exception:
            raw = None
        if not raw:
            continue
        try:
            t = _load(raw)
        except Exception:
            continue
        if _fresh(t):
            t.pool_slot = slot
            _local[slot] = (t, time.time())
            return t

    # 2) свежих нет — захватываем сами, но только один воркер на слот
    for slot in slots:
        try:
            got = bool(r.set(_LOCK.format(slot), "1", nx=True, ex=180))
        except Exception:
            got = False
        if not got:
            continue
        try:
            return await _capture_into(r, slot)
        finally:
            try:
                r.delete(_LOCK.format(slot))
            except Exception:
                pass

    # 3) все слоты кто-то уже захватывает — ждём чужой результат, а не жжём свой браузер
    deadline = time.time() + LOCK_WAIT_SEC
    while time.time() < deadline:
        await asyncio.sleep(2)
        for slot in slots:
            try:
                raw = r.get(_KEY.format(slot))
            except Exception:
                raw = None
            if not raw:
                continue
            try:
                t = _load(raw)
            except Exception:
                continue
            if _fresh(t):
                t.pool_slot = slot
                _local[slot] = (t, time.time())
                return t

    logger.warning("[tokens] пул: никто не отдал сессию за отведённое время — захватываю сам")
    return await capture_session_tokens(url, cookies)


def invalidate(tokens: SessionTokens | None, reason: str = "") -> None:
    """Выбросить сессию из пула (FB её забанил/протухла) — следующий возьмёт свежую."""
    slot = getattr(tokens, "pool_slot", None)
    if slot is None:
        return
    _local.pop(slot, None)
    r = _redis()
    if r is None:
        return
    try:
        r.delete(_KEY.format(slot))
        logger.warning(f"[tokens] пул: слот {slot} сброшен ({reason})")
    except Exception:
        pass
