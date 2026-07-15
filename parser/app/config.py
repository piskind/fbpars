from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    proxy_http_gateway: str = "http://gost:8888"
    proxy_rotate_url: str
    proxy_rotate_wait_sec: int = 20

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
    # Hard ceiling on ads collected per (config, date-chunk) pagination run. This is a
    # safety cap against a broken/looping cursor — NOT a target. Set high enough that on
    # a normal daily/weekly slice FB itself signals has_next=False before we hit it.
    # (The old default of 2000 was cutting collection off before FB was done.)
    max_ads_per_chunk: int = 70000

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
    # RQ job hard timeout (seconds) for one chunk / one media task.
    parse_job_timeout: int = 3600
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