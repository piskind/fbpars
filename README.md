# Spy

FB Ads Library parser + admin + dashboard.

## Quickstart (dev)

    cp .env.example .env
    # заполни .env (обязательно JWT_SECRET и PROXY_* — см. ниже)
    docker compose up --build

## Architecture (short)

- **api/** — FastAPI admin/dashboard backend.
- **web/** — React + Vite admin/dashboard.
- **parser/** — collection:
  - `spy_parser` (`python -m app.main`) — coordinator + discovery poll loop. Watches
    `ParserRun(triggered)`, fans out one RQ chunk job per (config, date-chunk), monitors them.
  - `parser-worker` (×N) — pull chunk jobs off the `parse` queue; browser-less curl pagination
    with a Chromium fallback only for token capture.
  - `media-worker` — off the `media` queue (mostly idle: media is served as direct FB CDN URLs).
  - `refresh` (`python -m app.scheduler`) — daily refresh + parser triggers.

## Security (must-do before exposing the box)

- **Ports are loopback-only.** `gost` (open HTTP proxy) and `postgres` are published on
  `127.0.0.1` only. Never republish `8888`/`5432` on `0.0.0.0` — port 8888 was found by
  scanners and abused as a free relay through the paid fxdx upstream.
- **`JWT_SECRET` is mandatory** (no default). The API refuses to start without a strong value:

      openssl rand -hex 32   # put the result in .env as JWT_SECRET

- **Rotate the shipped credentials** before handing the box to a client — see
  `scripts/harden.sh` (sets a new Postgres password + admin password). The Postgres password
  is considered compromised (it was printed in logs historically); rotate it.
- The parser masks the DB password in its startup log (`DB: postgresql+asyncpg://spy:***@…`).

## Prod tuning checklist (run after every `git pull`)

`.env` and `docker-compose.override.yml` are **gitignored**, so they survive a pull. The tracked
`docker-compose.yml` does NOT — keep prod-only compose tuning in the override file:

1. `.env` present with prod values, at least:
   - `CHUNK_DAYS=1`
   - `MAX_ADS_PER_CHUNK=…`
   - `PARSER_WORKERS=1`  (see proxy scaling below)
   - `JWT_SECRET=…`, `PROXY_*=…`, `DATABASE_URL=…`
2. `docker-compose.override.yml` present (copy from `docker-compose.override.yml.example`) with
   the `parser-worker` memory limit (prod: `6144m`) and `replicas`.
3. Rebuild/apply:

       docker compose up -d --build

4. Migrations run automatically via the `migrate` service; to run them manually:

       docker compose run --rm migrate      # names the exact failing step if any

> The old post-pull `sed 's/chunk:/chunk_/g …'` over `parser/app/` is **retired** — RQ job ids
> are now colon-free in code (`chunk_<cfg>_<from>_<to>_<run>`, `media_<ad>`). Do **not** run it;
> it used to corrupt Python tokens (`track_chunk:` → `track_chunk_`) and break `worker.py`.

## Proxy & worker scaling

The proxy is a **single fxdx exit channel** shared by all workers (`gost:8888`). With one exit-IP,
running multiple workers does NOT speed collection up — it just burns FB's rate limit twice as
fast and makes workers fight over IP rotation. **With one channel, run `PARSER_WORKERS=1`.**

To actually parallelise, expand the fxdx tariff to multiple upstreams, run one `gost` per
upstream, and list them in `.env`:

    PROXY_HTTP_GATEWAYS=["http://gost:8888","http://gost2:8888"]

Each worker then binds to one channel (`proxy.worker_gateway`, by hostname hash), so exit-IPs
are spread across workers — no code change needed. (Per-channel rotate URLs are the only extra
config to wire when that tariff lands.) Empty/one entry → current single-channel behaviour.

## Ops scripts

- **Orphaned cursor cleanup** (after editing a config's date range — removes `chunk_progress`
  rows that no longer match the current slicing; never touches `ads`):

      docker compose exec parser python -m app.cleanup_chunk_progress            # dry run
      docker compose exec parser python -m app.cleanup_chunk_progress --apply     # delete

- **Credential rotation:** `scripts/harden.sh` (needs `NEW_PG_PASSWORD`, `NEW_ADMIN_PASSWORD`).
