#!/bin/sh
# Run idempotent one-off migrations. Each script is safe to re-run.
# Fail-fast only after printing which step broke.
set -e
for f in \
  add_is_targeted_country.py \
  add_direct_media_urls.py \
  add_chunk_progress.py \
  add_ad_library_country_unique.py \
  add_sort_fields.py
do
  echo ">>> running $f"
  python "/db/migrations/$f"
done
echo ">>> all migrations OK"
