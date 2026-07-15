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


_IMPERSONATE_CANDIDATES: list[str] | None = None
_IMPERSONATE_IDX = 0


def _impersonate_candidates() -> list[str]:
    """Ordered impersonate targets: the configured one first, then a ladder of
    older Chrome fingerprints as fallbacks.

    settings.curl_impersonate (default "chrome131") is unsupported on curl_cffi
    0.7.x. This bites in two ways: (1) the target is missing from BrowserType, and
    (2) the enum lists it but the bundled libcurl-impersonate binary doesn't
    actually implement it. Case (2) only surfaces at request time as
    `Failed to setopt 47 1, curl: (43)`, so the enum check alone can't prevent it —
    _curl_fetch advances down this ladder when a POST hits that error.
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


async def _paginate_curl(
    url: str,
    tokens: SessionTokens,
    ad_nodes: list[dict],
    seen_ids: set[str],
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
        while len(ad_nodes) < max_ads:
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
                new_count, next_cursor, has_next = _collect_nodes(text, ad_nodes, seen_ids)
            except Exception as exc:
                raise RuntimeError(f"[curl] page={pages} parse error: {exc}\n{text[:300]}")

            logger.info(
                f"[curl] page={pages} new={new_count} total={len(ad_nodes)} has_next={has_next}"
            )

            if not next_cursor:
                logger.info("[curl] no cursor — end of results")
                return cursor, True, pages
            cursor = next_cursor
            if not has_next:
                logger.info(f"[curl] pagination complete: {len(ad_nodes)} ads")
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
    ad_nodes: list[dict],
    seen_ids: set[str],
    start_cursor: str | None,
    max_ads: int,
    session_id: str,
) -> tuple[str | None, bool, int]:
    """Fallback: paginate via in-page fetch() in a resource-blocked browser tab.

    Tokens come from the fetch session's OWN page, so lsd/form-template match its cookies.
    """
    cursor = start_cursor
    pages = 0
    rl_hits = 0

    async with browser_fetch_session(url) as (fetch, tokens):
        while len(ad_nodes) < max_ads:
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
            new_count, next_cursor, has_next = _collect_nodes(text, ad_nodes, seen_ids)
            logger.info(
                f"[browser-fetch] page={pages} new={new_count} total={len(ad_nodes)} has_next={has_next}"
            )

            if not next_cursor:
                return cursor, True, pages
            cursor = next_cursor
            if not has_next:
                return cursor, True, pages

            await asyncio.sleep(
                random.uniform(settings.pagination_delay_min, settings.pagination_delay_max)
            )

    # See _paginate_curl: chunk-cap exit is not a natural end → done=False.
    return cursor, False, pages


async def paginate(url: str, max_ads: int | None = None, start_cursor: str | None = None) -> list[dict]:
    """Capture tokens once, then paginate browser-less over the whole result set.

    Session tokens are refreshed every settings.session_refresh_every pages to avoid
    token staleness. Mode is settings.pagination_mode:
      "curl"    → curl_cffi only
      "browser" → thin browser fetch only
      "auto"    → curl_cffi, fall back to browser on repeated rate limits (default)

    max_ads is a safety ceiling (defaults to settings.max_ads_per_chunk); real
    completion is FB signalling has_next=False.
    """
    if max_ads is None:
        max_ads = settings.max_ads_per_chunk
    mode = settings.pagination_mode
    if mode == "curl" and not _CURL_AVAILABLE:
        logger.warning("[paginate] curl_cffi not installed — falling back to browser mode")
        mode = "browser"

    ad_nodes: list[dict] = []
    seen_ids: set[str] = set()
    session_id = str(uuid.uuid4())
    cursor = start_cursor
    total_pages = 0
    tokens: SessionTokens | None = None
    use_curl = mode in ("curl", "auto") and _CURL_AVAILABLE

    natural_end = False
    while len(ad_nodes) < max_ads:
        # Curl needs tokens up front; browser-only mode captures its own in-session.
        # (Re)capture on first pass and every session_refresh_every pages.
        if use_curl and (tokens is None or (total_pages and total_pages % settings.session_refresh_every == 0)):
            tokens = await capture_session_tokens(url)
            logger.info(f"[paginate] session tokens ready (pages so far={total_pages})")

        # Chunk pagination so session-refresh boundaries are respected without losing cursor.
        remaining_to_refresh = settings.session_refresh_every - (total_pages % settings.session_refresh_every)
        chunk_cap = len(ad_nodes) + max(1, remaining_to_refresh) * 40  # ~40 ads/page ceiling
        chunk_max = min(max_ads, chunk_cap)
        done = False
        try:
            if use_curl:
                cursor, done, pages = await _paginate_curl(
                    url, tokens, ad_nodes, seen_ids, cursor, chunk_max, session_id
                )
            else:
                # Browser path captures its own in-session tokens.
                cursor, done, pages = await _paginate_browser(
                    url, ad_nodes, seen_ids, cursor, chunk_max, session_id
                )
        except RateLimited as exc:
            if mode == "auto" and use_curl:
                logger.warning(f"[paginate] curl exhausted ({exc}) — switching to browser fallback")
                cursor, done, pages = await _paginate_browser(
                    url, ad_nodes, seen_ids, cursor, chunk_max, session_id
                )
            else:
                logger.error(f"[paginate] rate limited with no fallback available: {exc}")
                raise

        total_pages += pages
        if done or not cursor:
            natural_end = True
            break
        # Force a fresh token session on the next loop iteration.
        tokens = None

    if natural_end:
        logger.info(
            f"[paginate] done: has_next=False, natural end ({len(ad_nodes)} ads) "
            f"over {total_pages} pages (mode={mode})"
        )
    else:
        logger.warning(
            f"[paginate] stopped by max_ads cap ({max_ads}) — FB may have more data "
            f"({len(ad_nodes)} ads over {total_pages} pages, mode={mode})"
        )
    return ad_nodes


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
        logger.info(f"[smoke] mode={settings.pagination_mode} curl_available={_CURL_AVAILABLE} impersonate={resolved}")
        logger.info(f"[smoke] URL: {url}")
        nodes = await paginate(url, max_ads=30)
        logger.info(f"[smoke] RAW nodes collected: {len(nodes)}")
        for n in nodes[:3]:
            logger.info(f"  id={n.get('id')} active={n.get('is_active')} page={n.get('page_name')!r}")
        if not nodes:
            logger.error("[smoke] RAW=0 — pagination collected nothing")

    _asyncio.run(_smoke())
