#!/bin/sh
# Keep an RQ worker alive inside the container. Docker `restart:` still covers
# full container death (OOM kill, host reboot); this loop covers process crashes
# where the shell would otherwise exit and leave a gap before Docker recreates.
set -eu
QUEUE="${1:?usage: rq_watchdog.sh <queue>}"
URL="${REDIS_URL:-redis://spy_redis:6379/0}"

while true; do
  echo "[rq-watchdog] starting rq worker queue=${QUEUE} url=${URL}"
  # --with-scheduler is REQUIRED for Retry(interval=...) to actually re-run: RQ puts an
  # interval-delayed retry in the ScheduledJobRegistry, and only a running scheduler promotes
  # it back onto the queue. Without it, a rate-limited chunk was "scheduled to retry" and then
  # never picked up — the worker went idle and the day was under-collected. Each worker runs a
  # scheduler thread; RQ serialises them with a Redis lock, so enabling it on all is safe.
  # Background + pidfile so a healthcheck can verify the worker is alive.
  rq worker --with-scheduler --url "$URL" "$QUEUE" &
  pid=$!
  echo "$pid" > /tmp/rq-worker.pid
  set +e
  wait "$pid"
  code=$?
  set -e
  rm -f /tmp/rq-worker.pid
  echo "[rq-watchdog] rq worker exited code=${code} — restarting in 3s"
  sleep 3
done
