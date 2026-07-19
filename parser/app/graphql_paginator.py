"""Browser-less pagination over FB Ad Library GraphQL.

Phase 1 of the scaling refactor: the browser is used ONLY to capture session tokens
(app.browser.capture_session_tokens); pagination itself runs over curl_cffi with a
Chrome TLS fingerprint. If curl_cffi keeps tripping FB's rate-limit / challenge, we fall
back to a thin resource-blocked browser tab that runs fetch() in-page.

Reuses the existing parse helpers from graphql_client so the response shape and the
downstream map_graphql_card path are untouched.
"""
import asyncio
import random
import uuid
from typing import Awaitable, Callable
from urllib.parse import urlencode

from loguru import logger

from app.config import settings
from app.browser import SessionTokens, capture_session_tokens, browser_fetch_session
from app.graphql_client import _build_form_data, _parse_response_json, _extract_ads_and_cursor
from app import proxy as proxy_mod

_GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
_RATE_LIMIT_CODE = "1675004"
_RATE_LIMIT_PAUSE = 30

# curl_cffi is imported lazily so the module still imports where it isn't installed
# (e.g. during static checks); pagination_mode="browser" never touches it.
try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession  # type: ignore
    _CURL_AVAILABLE = True
except Exception:  # pragma: no cover - import-time guard
    _CurlAsyncSession = None  # type: ignore
    _CURL_AVAILABLE = False


class RateLimited(Exception):
    """curl_cffi request returned FB's 1675004 rate-limit sentinel."""


# Transient network faults (tunnel/proxy drop, connection reset, timeouts) — a single one
# of these must NOT kill a multi-hour chunk. paginate() retries the current segment with
# backoff (resuming from the saved cursor) instead of letting the chunk fail. These strings
# come from Chromium (net::ERR_*), curl_cffi (curl: (7|35|56)) and httpx.
_TRANSIENT_MARKERS = (
    "err_tunnel_connection_failed", "err_proxy_connection_failed",
    "err_connection_reset", "err_connection_closed", "err_connection_aborted",
    "err_connection_failed", "err_network_changed", "err_timed_out",
    "err_address_unreachable", "err_socks_connection_failed", "err_empty_response",
    "net::err_", "page load failed",
    "connection reset", "connection refused", "connection aborted",
    "connection timed out", "read timed out", "timed out", "timeout",
    "curl: (7)", "curl: (28)", "curl: (35)", "curl: (52)", "curl: (56)",
    # browser-fetch surfaces a bad upstream as "HTTP 5xx" — usually the exit-IP not being
    # ready right after a rotation; retry the segment rather than failing the whole chunk.
    "http 500", "http 502", "http 503", "http 504",
)


def _is_transient_net_error(exc: BaseException) -> bool:
    s = str(exc).lower()
    return any(m in s for m in _TRANSIENT_MARKERS)


_IMPERSONATE_CANDIDATES: list[str] | None = None
_IMPERSONATE_IDX = 0


def _impersonate_candidates() -> list[str]:
    """Ordered impersonate targets: the configured one first, then a ladder of
    older Chrome fingerprints as fallbacks.

    settings.curl_impersonate (default "chrome131") requires curl_cffi >= 0.8 — the
    pinned 0.15.0 ships it natively. The ladder stays as a safety net: a target can be
    missing from BrowserType (filtered below) or present in the enum yet not implemented
    by the bundled libcurl-impersonate binary, which only surfaces at request time as
    `Failed to setopt 47 1, curl: (43)`; _curl_fetch advances down this ladder when a POST
    hits that error.
    """
    global _IMPERSONATE_CANDIDATES
    if _IMPERSONATE_CANDIDATES is not None:
        return _IMPERSONATE_CANDIDATES

    ladder = [
        settings.curl_impersonate,
        "chrome124", "chrome120", "chrome116", "chrome110", "chrome107", "chrome99",
    ]
    try:
        from curl_cffi.requests import BrowserType
        valid = {b.value for b in BrowserType}
    except Exception:
        valid = None

    out: list[str] = []
    for t in ladder:
        if t and t not in out and (valid is None or t in valid):
            out.append(t)
    _IMPERSONATE_CANDIDATES = out or [settings.curl_impersonate]
    return _IMPERSONATE_CANDIDATES


def _resolve_impersonate() -> str:
    """Current best impersonate target (first candidate not yet ruled out)."""
    cands = _impersonate_candidates()
    return cands[min(_IMPERSONATE_IDX, len(cands) - 1)]


def _advance_impersonate() -> bool:
    """Rule out the current impersonate target; return True if another remains."""
    global _IMPERSONATE_IDX
    cands = _impersonate_candidates()
    if _IMPERSONATE_IDX < len(cands) - 1:
        _IMPERSONATE_IDX += 1
        logger.warning(f"[curl] impersonate fallback → '{cands[_IMPERSONATE_IDX]}'")
        return True
    return False


def _is_impersonate_setopt_error(exc: Exception) -> bool:
    """curl_cffi surfaces an unsupported impersonate target as a setopt failure
    (`Failed to setopt 47 1, curl: (43)` — CURLOPT_POST / BAD_FUNCTION_ARGUMENT —
    or ImpersonateError)."""
    s = str(exc).lower()
    return "setopt" in s or "impersonate" in s or "(43)" in s


def _build_variables(template: dict, cursor: str | None, session_id: str) -> dict:
    # Reuse the exact variables FB sent (country, adType, sortData, first, v, ...),
    # only overriding the pagination cursor and a stable session id.
    return {**template, "cursor": cursor, "sessionID": session_id}


def _curl_headers(lsd: str | None) -> dict:
    return {
        "content-type": "application/x-www-form-urlencoded",
        "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
        "x-fb-lsd": lsd or "",
        "x-asbd-id": "359341",
        "origin": "https://www.facebook.com",
        "referer": "https://www.facebook.com/ads/library/",
    }


async def _curl_fetch(
    session, tokens: SessionTokens, form_data: dict, proxy_url: str | None
) -> tuple[int, str]:
    """POST one pagination page via curl_cffi (TLS-impersonated)."""
    headers = _curl_headers(tokens.lsd)
    if tokens.cookies:
        headers["cookie"] = tokens.cookies
    # Pre-encode the form as a string with an explicit content-type — matches the browser's
    # AdLibrarySearchPaginationQuery POST exactly and avoids any dict-encoding edge cases.
    body = urlencode(form_data)
    kwargs = dict(
        data=body,
        headers=headers,
        timeout=settings.curl_timeout,
    )
    if proxy_url:
        # curl_cffi accepts a requests-style proxies dict; keep http+https on the same URL
        # (gost gateway is http://, residential pool entries may be socks5h://).
        kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

    # impersonate is set per-request (not on the session) so we can fall back to an
    # older Chrome fingerprint if this curl_cffi build rejects the configured target
    # with `Failed to setopt 47 1, curl: (43)`.
    while True:
        impersonate = _resolve_impersonate()
        try:
            resp = await session.post(_GRAPHQL_URL, impersonate=impersonate, **kwargs)
            return resp.status_code, resp.text
        except Exception as exc:
            if _is_impersonate_setopt_error(exc) and _advance_impersonate():
                logger.warning(f"[curl] impersonate '{impersonate}' rejected ({exc}) — retrying")
                continue
            raise


def _collect_nodes(text: str, ad_nodes: list[dict], seen_ids: set[str]) -> tuple[int, str | None, bool]:
    """Parse a response, append new nodes, return (new_count, next_cursor, has_next)."""
    parsed = _parse_response_json(text)
    nodes, next_cursor, has_next = _extract_ads_and_cursor(parsed)
    new_count = 0
    for node in nodes:
        nid = str(node.get("id") or "")
        if nid and nid not in seen_ids:
            seen_ids.add(nid)
            ad_nodes.append(node)
            new_count += 1
    return new_count, next_cursor, has_next


# Awaited with (batch of freshly-collected nodes, resume cursor, has_next) so the caller
# can persist them incrementally AND bookmark the cursor atomically with the cards —
# durability + resumability across a multi-hour pagination that may die mid-run.
# Returns a "progress" count = rows actually written to the DB (new+updated) PLUS write
# errors, so the stall guard can key off real DB writes instead of fetched-from-FB counts
# (a chunk can fetch thousands of already-saved cards → 0 written → must still be caught).
BatchCallback = Callable[[list[dict], "str | None", bool], Awaitable[int]]


class _BatchCollector:
    """Dedups nodes across the whole run and flushes them to on_batch in fixed-size
    batches, so a crash mid-pagination keeps everything committed so far instead of
    losing hours of scraping held only in the work-horse's memory.

    on_batch receives the current resume cursor + has_next so the caller can persist the
    pagination bookmark in the same transaction as the cards (cursor never runs ahead of
    saved data). When on_batch is None, retains every node (legacy: caller reads .nodes).
    seen_ids persists for the whole run (cross-batch dedup, optionally pre-seeded from the
    DB); the node buffer is cleared on every flush to keep memory flat.
    """

    def __init__(self, on_batch: BatchCallback | None, batch_size: int, seen_ids: set[str] | None = None):
        self.on_batch = on_batch
        self.batch_size = max(1, batch_size)
        self.seen_ids: set[str] = seen_ids if seen_ids is not None else set()
        self.total = 0  # unique NEW nodes collected this run (excludes pre-seeded dedups)
        self.progress_total = 0  # sum of on_batch returns = DB writes (new+updated) + write errors
        self.cursor: str | None = None  # cursor to resume from (next unfetched page)
        self.has_next = True
        self._buffer: list[dict] = []  # collected since last flush
        self._retained: list[dict] = []  # everything, only when on_batch is None
        self._stall_pages = 0       # pages since progress_total last advanced (stall guard)
        self._last_progress = 0     # tracked on the collector so it survives session-refresh
                                    # segment boundaries (a per-call local would reset and never trip)

    def add_response(self, text: str) -> tuple[int, str | None, bool]:
        new_count, cursor, has_next = _collect_nodes(text, self._buffer, self.seen_ids)
        self.total += new_count
        self.cursor = cursor
        self.has_next = has_next
        return new_count, cursor, has_next

    async def maybe_flush(self, *, force: bool = False) -> None:
        # force=True always fires (even with an empty buffer) so the terminal cursor /
        # has_next=false is bookmarked at the end of a chunk; otherwise flush only on a
        # full batch.
        if not force and len(self._buffer) < self.batch_size:
            return
        batch = self._buffer
        self._buffer = []  # detach before awaiting so a failed batch can't be double-sent
        if self.on_batch is not None:
            written = await self.on_batch(batch, self.cursor, self.has_next)  # persist cards + cursor
            if isinstance(written, int):
                self.progress_total += written  # drives the DB-write-based stall guard
        else:
            self._retained.extend(batch)

    def note_page_and_check_stall(self, limit: int) -> bool:
        """Call once per fetched page (after maybe_flush). Returns True when the chunk has gone
        `limit` pages without a single DB write — the "FB replays already-saved cards forever"
        stall. State lives on the collector so it survives session-refresh segment boundaries."""
        if self.progress_total > self._last_progress:
            self._last_progress = self.progress_total
            self._stall_pages = 0
        else:
            self._stall_pages += 1
        return self._stall_pages >= limit

    @property
    def stall_pages(self) -> int:
        return self._stall_pages

    @property
    def nodes(self) -> list[dict]:
        return self._retained


async def _paginate_curl(
    url: str,
    tokens: SessionTokens,
    collector: "_BatchCollector",
    start_cursor: str | None,
    max_ads: int,
    session_id: str,
) -> tuple[str | None, bool, int]:
    """Paginate over curl_cffi until done / max_ads / repeated rate limits.

    Returns (last_cursor, done, pages_used). Raises RateLimited if curl trips the limit
    more than pagination_curl_fallback_after times in a row (caller may switch to browser).
    """
    cursor = start_cursor
    pages = 0
    consecutive_rl = 0

    async with _CurlAsyncSession() as session:
        while collector.total < max_ads:
            await proxy_mod.rotate_before_request()
            proxy_url = await proxy_mod.get_proxy_url()
            variables = _build_variables(tokens.variables_template, cursor, session_id)
            form_data = _build_form_data(tokens.as_tokens_dict(), variables)

            try:
                status, text = await _curl_fetch(session, tokens, form_data, proxy_url)
            except Exception as exc:
                consecutive_rl += 1
                logger.warning(f"[curl] page={pages + 1} transport error: {exc} (streak={consecutive_rl})")
                if consecutive_rl > settings.pagination_curl_fallback_after:
                    raise RateLimited(f"curl transport failed {consecutive_rl}x") from exc
                await asyncio.sleep(_RATE_LIMIT_PAUSE)
                continue

            if _RATE_LIMIT_CODE in text:
                consecutive_rl += 1
                logger.warning(
                    f"[curl] page={pages + 1} rate limit {_RATE_LIMIT_CODE} (streak={consecutive_rl})"
                )
                if consecutive_rl > settings.pagination_curl_fallback_after:
                    raise RateLimited(f"curl rate limited {consecutive_rl}x")
                # Provider decides: cool down this pool IP, or rotate the single channel.
                await proxy_mod.report_rate_limited(proxy_url)
                continue

            if status != 200:
                consecutive_rl += 1
                logger.warning(f"[curl] page={pages + 1} HTTP {status}: {text[:200]}")
                if consecutive_rl > settings.pagination_curl_fallback_after:
                    raise RateLimited(f"curl non-200 {consecutive_rl}x (last={status})")
                await asyncio.sleep(_RATE_LIMIT_PAUSE)
                continue

            consecutive_rl = 0
            pages += 1
            try:
                new_count, next_cursor, has_next = collector.add_response(text)
            except Exception as exc:
                raise RuntimeError(f"[curl] page={pages} parse error: {exc}\n{text[:300]}")

            # fetched_* = pulled from FB in this pagination session (NOT rows written to DB —
            # that's the "committed batch +N cards" line). Disambiguated because both used to
            # say "new" and a session with fetched_new>0 but committed 0 looked like progress.
            logger.info(
                f"[curl] page={pages} fetched_new={new_count} fetched_total={collector.total} "
                f"has_next={has_next}"
            )
            await collector.maybe_flush()  # commit each commit_batch_size cards as we go

            # Stall guard keyed off DB WRITES, not fetched_new: the pathological case is FB
            # replaying already-saved cards (fetched_new>0 every page) that all land as
            # skipped_already_reviewed → 0 rows written for hundreds of pages. Counting fetched
            # ads never tripped it. progress_total advances only when a committed batch actually
            # wrote rows (or hit write errors, which reset it so a broken chunk isn't mistaken
            # for an exhausted one). N pages with no DB progress → close the chunk.
            if collector.note_page_and_check_stall(settings.pagination_stall_pages):
                logger.warning(
                    f"[curl] stall guard: {collector.stall_pages} pages with no DB writes "
                    f"(fetched {collector.total} ads, all already-saved/skipped) — closing chunk"
                )
                return cursor, True, pages

            if not next_cursor:
                logger.info("[curl] no cursor — end of results")
                return cursor, True, pages
            cursor = next_cursor
            if not has_next:
                logger.info(f"[curl] pagination complete: {collector.total} ads")
                return cursor, True, pages

            await asyncio.sleep(
                random.uniform(settings.pagination_delay_min, settings.pagination_delay_max)
            )

    # Loop exited on the chunk cap (chunk_max = this session-refresh window), NOT because
    # FB is out of data. Report done=False so paginate() rolls into the next chunk; the
    # real global stop (settings.max_ads_per_chunk) is owned by paginate()'s own while-loop.
    return cursor, False, pages


async def _paginate_browser(
    url: str,
    collector: "_BatchCollector",
    start_cursor: str | None,
    max_ads: int,
    session_id: str,
) -> tuple[str | None, bool, int]:
    """Fallback: paginate via in-page fetch() in a resource-blocked browser tab.

    Tokens come from the fetch session's OWN page, so lsd/form-template match its cookies.
    The Playwright context is recycled every settings.browser_recycle_pages pages
    (continuing from the saved cursor) so RSS stays flat over long paginations instead of
    climbing to 6+ GB when one page is held across hundreds of requests.
    """
    cursor = start_cursor
    pages = 0
    rl_hits = 0
    recycle_every = max(1, settings.browser_recycle_pages)

    while collector.total < max_ads:
        # (Re)open a fresh browser context; continue from the cursor we've reached so far.
        async with browser_fetch_session(url) as (fetch, tokens):
            session_pages = 0
            while collector.total < max_ads and session_pages < recycle_every:
                variables = _build_variables(tokens.variables_template, cursor, session_id)
                form_data = _build_form_data(tokens.as_tokens_dict(), variables)
                status, text = await fetch(form_data, tokens.lsd)

                if _RATE_LIMIT_CODE in text:
                    rl_hits += 1
                    if rl_hits > settings.pagination_curl_fallback_after:
                        raise RateLimited(f"browser-fetch rate limited {rl_hits}x")
                    logger.warning(f"[browser-fetch] page={pages + 1} rate limit — pause {_RATE_LIMIT_PAUSE}s")
                    await asyncio.sleep(_RATE_LIMIT_PAUSE)
                    continue

                if status != 200:
                    raise RuntimeError(f"[browser-fetch] page={pages + 1} HTTP {status}: {text[:200]}")

                pages += 1
                session_pages += 1
                new_count, next_cursor, has_next = collector.add_response(text)
                # fetched_* = pulled from FB this session, not DB writes (see 'committed batch').
                logger.info(
                    f"[browser-fetch] page={pages} fetched_new={new_count} "
                    f"fetched_total={collector.total} has_next={has_next}"
                )
                await collector.maybe_flush()  # commit each commit_batch_size cards as we go

                # Stall guard keyed off DB writes (see _paginate_curl): close once N pages pass
                # with nothing written (already-saved cards replayed as skipped_already_reviewed).
                if collector.note_page_and_check_stall(settings.pagination_stall_pages):
                    logger.warning(
                        f"[browser-fetch] stall guard: {collector.stall_pages} pages with no DB "
                        f"writes (fetched {collector.total} ads, all already-saved/skipped) — closing chunk"
                    )
                    return cursor, True, pages

                if not next_cursor:
                    return cursor, True, pages
                cursor = next_cursor
                if not has_next:
                    return cursor, True, pages

                await asyncio.sleep(
                    random.uniform(settings.pagination_delay_min, settings.pagination_delay_max)
                )

        if collector.total >= max_ads:
            break
        logger.info(
            f"[browser-fetch] recycling browser context after {session_pages} pages "
            f"(cursor kept, total={collector.total})"
        )

    # See _paginate_curl: chunk-cap exit is not a natural end → done=False.
    return cursor, False, pages


async def paginate(
    url: str,
    max_ads: int | None = None,
    start_cursor: str | None = None,
    on_batch: BatchCallback | None = None,
    seen_ids: set[str] | None = None,
) -> list[dict]:
    """Capture tokens once, then paginate browser-less over the whole result set.

    Session tokens are refreshed every settings.session_refresh_every pages to avoid
    token staleness. Mode is settings.pagination_mode:
      "curl"    → curl_cffi only
      "browser" → thin browser fetch only
      "auto"    → curl_cffi, fall back to browser on repeated rate limits (default)

    max_ads is a safety ceiling (defaults to settings.max_ads_per_chunk); real
    completion is FB signalling has_next=False.

    on_batch (optional): awaited with each batch of settings.commit_batch_size collected
    nodes so the caller can persist them as we go. With it set, only the current batch is
    held in memory and a crash keeps every already-committed batch — the return value is
    then empty (nodes were handed off via on_batch). Without it, all nodes are retained
    and returned (legacy).
    """
    if max_ads is None:
        max_ads = settings.max_ads_per_chunk
    mode = settings.pagination_mode
    if mode == "curl" and not _CURL_AVAILABLE:
        logger.warning("[paginate] curl_cffi not installed — falling back to browser mode")
        mode = "browser"

    collector = _BatchCollector(on_batch, settings.commit_batch_size, seen_ids=seen_ids)
    session_id = str(uuid.uuid4())
    cursor = start_cursor
    total_pages = 0
    tokens: SessionTokens | None = None
    use_curl = mode in ("curl", "auto") and _CURL_AVAILABLE

    natural_end = False
    transient_streak = 0
    try:
        while collector.total < max_ads:
            try:
                # Curl needs tokens up front; browser-only mode captures its own in-session.
                # (Re)capture on first pass and every session_refresh_every pages.
                if use_curl and (tokens is None or (total_pages and total_pages % settings.session_refresh_every == 0)):
                    tokens = await capture_session_tokens(url)
                    logger.info(f"[paginate] session tokens ready (pages so far={total_pages})")

                # Chunk pagination so session-refresh boundaries are respected without losing cursor.
                remaining_to_refresh = settings.session_refresh_every - (total_pages % settings.session_refresh_every)
                chunk_cap = collector.total + max(1, remaining_to_refresh) * 40  # ~40 ads/page ceiling
                chunk_max = min(max_ads, chunk_cap)
                done = False
                try:
                    if use_curl:
                        cursor, done, pages = await _paginate_curl(
                            url, tokens, collector, cursor, chunk_max, session_id
                        )
                    else:
                        # Browser path captures its own in-session tokens.
                        cursor, done, pages = await _paginate_browser(
                            url, collector, cursor, chunk_max, session_id
                        )
                except RateLimited as exc:
                    if mode == "auto" and use_curl:
                        logger.warning(f"[paginate] curl exhausted ({exc}) — switching to browser fallback")
                        cursor, done, pages = await _paginate_browser(
                            url, collector, cursor, chunk_max, session_id
                        )
                    else:
                        logger.error(f"[paginate] rate limited with no fallback available: {exc}")
                        raise
            except RateLimited:
                raise  # a real rate-limit dead-end, not a transient blip — let it fail the chunk
            except Exception as exc:
                # Transient tunnel/proxy/timeout blip: retry the segment from the saved cursor
                # with growing backoff instead of killing the chunk (the shared exit IP drops
                # when another worker rotates mid-request). Only after net_transient_retries do
                # we give up and let the chunk fail (→ RQ retry).
                if _is_transient_net_error(exc) and transient_streak < settings.net_transient_retries:
                    transient_streak += 1
                    backoff = settings.net_transient_backoff_sec * transient_streak
                    logger.warning(
                        f"[paginate] transient network error "
                        f"(retry {transient_streak}/{settings.net_transient_retries} in {backoff:.0f}s, "
                        f"resuming from cursor, total={collector.total}): {exc}"
                    )
                    await asyncio.sleep(backoff)
                    tokens = None  # force a fresh token session
                    continue
                raise

            transient_streak = 0  # a clean segment resets the transient budget
            total_pages += pages
            if done or not cursor:
                natural_end = True
                break
            # Force a fresh token session on the next loop iteration.
            tokens = None
    except BaseException:
        # Durability: commit whatever's buffered before the failure propagates, so a
        # crash mid-run keeps the partial batch too (best-effort — never mask the error).
        await _safe_final_flush(collector)
        raise

    # Natural end (FB signalled has_next=False, ran out of cursor, or the stall guard fired):
    # force the terminal bookmark to has_next=False so enqueue_run skips this chunk next run.
    # Without this, a "no cursor but has_next still True" end left the chunk resumable forever.
    if natural_end:
        collector.has_next = False
    # Normal completion: flush the tail batch; a failure here should fail the chunk.
    await collector.maybe_flush(force=True)

    if natural_end:
        logger.info(
            f"[paginate] done: has_next=False, natural end ({collector.total} ads) "
            f"over {total_pages} pages (mode={mode})"
        )
    else:
        logger.warning(
            f"[paginate] stopped by max_ads cap ({max_ads}) — FB may have more data "
            f"({collector.total} ads over {total_pages} pages, mode={mode})"
        )
    return collector.nodes


async def _safe_final_flush(collector: "_BatchCollector") -> None:
    try:
        await collector.maybe_flush(force=True)
    except Exception as exc:
        logger.error(f"[paginate] durability flush failed (partial batch may be lost): {exc}")


if __name__ == "__main__":
    # Smoke test: PE / nutra / no keyword / total_impressions desc. Expect RAW > 0.
    #   PAGINATION_MODE=curl    python -m app.graphql_paginator
    #   PAGINATION_MODE=browser python -m app.graphql_paginator
    import asyncio as _asyncio
    from app.browser import build_library_url

    async def _smoke():
        url = build_library_url(
            country="PE", keyword=None, languages=None,
            active_status="all", media_type="all",
            sort_mode="total_impressions", sort_direction="desc",
        )
        resolved = _resolve_impersonate() if _CURL_AVAILABLE else "n/a"
        try:
            import curl_cffi as _cc
            _ver = _cc.__version__
        except Exception:
            _ver = "n/a"
        logger.info(
            f"[smoke] mode={settings.pagination_mode} curl_available={_CURL_AVAILABLE} "
            f"curl_cffi={_ver} impersonate={resolved}"
        )
        logger.info("[smoke] run with PAGINATION_MODE=curl to assert the fast path (no browser fallback)")
        logger.info(f"[smoke] URL: {url}")
        nodes = await paginate(url, max_ads=30)
        logger.info(f"[smoke] RAW nodes collected: {len(nodes)}")
        for n in nodes[:3]:
            logger.info(f"  id={n.get('id')} active={n.get('is_active')} page={n.get('page_name')!r}")
        if not nodes:
            logger.error("[smoke] RAW=0 — pagination collected nothing")

    _asyncio.run(_smoke())
