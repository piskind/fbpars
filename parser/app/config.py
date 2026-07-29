from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    proxy_http_gateway: str = "http://gost:8888"
    # Multiple upstream gost channels (one per fxdx exit-IP). Today there's ONE exit-IP shared
    # by every worker — so parallelism just burns FB's limit twice as fast and makes workers
    # fight over rotation. When the fxdx tariff is expanded, run one gost per upstream and list
    # them here (JSON, e.g. ["http://gost:8888","http://gost2:8888"]); each worker then binds to
    # ONE channel (proxy.worker_gateway) so exit-IPs are spread across workers. Empty (default)
    # → the single PROXY_HTTP_GATEWAY, i.e. current behaviour unchanged.
    proxy_http_gateways: list[str] = []
    proxy_rotate_url: str
    proxy_rotate_wait_sec: int = 20
    # ── IP-rotation storm control (single shared exit IP via fxdx/gost) ──
    # All workers share ONE exit IP, so a rotation changes it for everyone. When several
    # workers hit a rate limit at once they used to all call the rotate URL together →
    # fxdx returns "429 Too Many Requests" and the rotation never lands. A Redis lock makes
    # rotation exclusive; a global cooldown throttles how often the shared IP flips; a 429
    # backoff waits fxdx out; a settle pause after a change lets the new upstream come up
    # (kills the "IP check failed: 503" right after a rotation).
    rotate_lock_ttl_sec: int = 30      # max time one worker may hold the rotate lock
    rotate_cooldown_sec: int = 45      # min gap between shared-IP rotations across all workers
    rotate_429_backoff_sec: int = 60   # wait this long when the rotate URL answers 429
    rotate_settle_sec: int = 8         # pause after a successful change before hitting FB again

    s3_endpoint_url: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str = "ru-1"

    log_level: str = "INFO"
    headless: bool = True
    scroll_max_attempts: int = 50
    scroll_pause_sec: int = 2
    use_graphql: bool = True
    graphql_mode: str = "fetch"  # "fetch" = in-page fetch pagination; "intercept" = scroll + response capture

    # ── Phase 1: browser-less pagination ────────────────────────────────
    # "curl"    = curl_cffi only (TLS-impersonated), no browser in pagination
    # "browser" = thin browser fetch() only (blank tab, resources blocked)
    # "auto"    = curl_cffi first, fall back to thin browser on repeated rate limits
    pagination_mode: str = "auto"
    # curl_cffi impersonation profile (Chrome TLS/JA3 fingerprint)
    curl_impersonate: str = "chrome131"
    # Recreate the token session every N pages to avoid token staleness / leaks
    session_refresh_every: int = 50
    # Concurrent Chromium instances allowed *only* during token capture (short-lived)
    token_browser_concurrency: int = 2
    # Consecutive rate-limit / challenge hits on curl before falling back to browser
    pagination_curl_fallback_after: int = 3
    # Per-request delay between pagination calls (seconds, randomised in range)
    pagination_delay_min: float = 1.5
    pagination_delay_max: float = 3.5
    # curl_cffi per-request timeout (seconds)
    curl_timeout: int = 30
    # Stall guard: after this many CONSECUTIVE pages that add ZERO *new* ads (FB looping the
    # same nodes while still claiming has_next=True), treat the chunk as exhausted and close
    # it — otherwise it re-runs forever, re-chewing collected data while other days wait.
    # Note: the streak resets on ANY page with new>0, so a genuinely-collecting chunk (even a
    # 1990-page one) never trips this — only a real loop does (the pathological case seen was
    # 114 in a row). Set generously above the largest already-saved prefix a fresh-restarted
    # chunk might re-scan before new cards appear. Tune via PAGINATION_STALL_PAGES.
    pagination_stall_pages: int = 60
    # A single transient network blip (ERR_TUNNEL_CONNECTION_FAILED / ERR_PROXY_CONNECTION_FAILED
    # / curl 7,35,56 …) should NOT kill a multi-hour chunk. Retry the current pagination
    # segment (resuming from the saved cursor) this many times with growing backoff first.
    net_transient_retries: int = 4
    net_transient_backoff_sec: float = 5.0
    # Hard ceiling on ads collected per (config, date-chunk) pagination run. This is a
    # safety cap against a broken/looping cursor — NOT a target. Set high enough that on
    # a normal daily/weekly slice FB itself signals has_next=False before we hit it.
    # (The old default of 2000 was cutting collection off before FB was done.)
    max_ads_per_chunk: int = 70000
    # Commit scraped cards to the DB every N collected during pagination, instead of
    # holding the whole run in memory and upserting once at the end. A multi-hour run
    # that dies (zombie browser / OOM / killed work-horse) then keeps everything committed
    # so far instead of losing all of it.
    commit_batch_size: int = 200
    # Preload already-saved library_ids for the country into the paginator dedup set (skips
    # re-upserting them). Set False to force a full deep RE-COLLECT of an already-partially-
    # collected country: re-scanned cards land as `updated` (=DB writes) so the stall guard
    # stays alive through the collected prefix and pagination reaches the un-collected tail.
    preload_seen_ids: bool = True
    # Recycle the browser-fetch Playwright context every N pages during pagination
    # (continuing from the saved cursor), so RSS stays flat (~2 GB) instead of climbing
    # to 6+ GB when a single page is held across hundreds of pagination requests.
    browser_recycle_pages: int = 50

    # ── Phase 2: Redis + RQ task queue ──────────────────────────────────
    redis_url: str = "redis://spy_redis:6379/0"
    # When True, the discovery trigger fans out into RQ chunk jobs (scalable path).
    # When False, falls back to the legacy in-process run_once().
    use_queue: bool = True
    # Split a config's date range into chunks of this many days (1 = daily, bypasses
    # FB's ~1700-card cap and makes crash-resume cheap — re-run one day, not the whole geo).
    chunk_days: int = 1
    # Number of RQ parser worker replicas (scaled in docker-compose).
    parser_workers: int = 4
    # RQ job hard timeout (seconds). Doubles as how long an ORPHANED chunk — one whose
    # work-horse was OOM/SIGKILL-ed without the parent worker marking it failed — sits in the
    # StartedJobRegistry before cleanup reaps it and Retry re-runs it on another worker. Kept
    # moderate (was 3600) so a crashed chunk is auto-picked-back-up in ≤30min instead of ≤1h.
    # Safe to lower because pagination commits every commit_batch_size cards and resumes from
    # chunk_progress.last_cursor — a killed+retried chunk continues from its bookmark, it does
    # NOT restart the day. If you see HEALTHY long chunks getting cut mid-collection (log shows
    # a timeout while has_next=true and cards still committing), raise this back toward 3600.
    parse_job_timeout: int = 1800
    media_job_timeout: int = 1800

    # ── Phase 4: media pipeline ─────────────────────────────────────────
    # When True, parse workers only write metadata and enqueue a media job per ad
    # (separate "media" queue + media-worker). When False, media is uploaded inline
    # (legacy behaviour) — useful for the non-queue fallback path.
    split_media_pipeline: bool = True
    # Skip video downloads on the first pass — video is the heaviest traffic and the
    # server has a transfer cap. A second pass / on-demand can fetch video later.
    skip_video_first_pass: bool = True
    media_workers: int = 2
    # Master switch for downloading media into S3. Default OFF: we store the direct
    # FB CDN URLs on the Ad and let the client's browser load/download them straight
    # from fbcdn.net (saves server traffic + storage). Flip to True to restore the
    # legacy S3 download/phash pipeline (Creative rows, media queue, media-worker).
    enable_media_download: bool = False

    # ── Phase 5: refresh worker ─────────────────────────────────────────
    # "browser"  = open each ad's page (legacy; also does EU reach extraction).
    # "graphql"  = experimental browser-less is_active batch check (see TODO in refresh_worker).
    refresh_mode: str = "browser"
    # How many library_ids to bundle per token session in graphql refresh mode.
    refresh_graphql_batch: int = 50

    # ── Phase 3: proxy — PLACEHOLDERS (no pool bought yet) ───────────────
    # "rotate_url" = single mobile channel, rotate by hitting a rotate URL (current fxdx style)
    # "pool"       = residential pool, one IP per request from PROXY_POOL / proxies table
    proxy_mode: str = "rotate_url"
    # Residential pool entries "host:port:user:pass" — fill when pool is bought
    proxy_pool: list[str] = []
    # If True and proxy_mode="pool" with empty PROXY_POOL, also load from `proxies` DB table
    proxy_pool_from_db: bool = True
    # Rotate proxy every N pagination requests (1 = every request, preventive)
    rotate_every_n_requests: int = 1


settings = Settings()