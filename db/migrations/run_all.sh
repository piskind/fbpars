#!/bin/sh
# Run idempotent one-off migrations (the `migrate` service). Each script is safe to re-run.
#
# Order matters where one migration depends on another's table/column:
#   - country-unique BEFORE sort_fields (a broken sort_fields must not block the schema fix)
#   - chunk_progress BEFORE add_chunk_stats (the latter adds columns to that table)
#
# On failure we print EXACTLY which script broke and stop (dash has no `trap ERR`, so we
# check each exit code explicitly — the old script only *claimed* to name the broken step).
set -u

MIGRATIONS="
add_is_targeted_country.py
add_direct_media_urls.py
add_chunk_progress.py
add_ad_library_country_unique.py
add_sort_fields.py
add_config_filter_audit.py
add_chunk_stats.py
"

for f in $MIGRATIONS; do
  echo ">>> running $f"
  if ! python "/db/migrations/$f"; then
    echo "!!! MIGRATION FAILED: $f — traceback above. Fix it, then re-run: docker compose run --rm migrate" >&2
    exit 1
  fi
done

echo ">>> all migrations OK"
