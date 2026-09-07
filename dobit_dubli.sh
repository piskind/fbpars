#!/bin/bash
# Досчитать отпечатки для строк, добавленных сбором, и пометить повторы.
# Между порциями пауза: в прошлый раз миграция вместе со сломанным дедупом уложила базу.
set -u
exec >>/root/fbpars/dubli.log 2>&1
echo "=== $(date -u +'%F %T UTC') добор отпечатков + пометка (щадящий режим) ==="

docker exec fbpars-parser-worker-1 python /app/dubli_migraciya.py --shag zapolnit
sleep 60
docker exec fbpars-parser-worker-1 python /app/dubli_migraciya.py --shag pometit
docker exec fbpars-parser-worker-1 python /app/dubli_migraciya.py --shag otchet

# Старый индекс по сломанному выражению больше не нужен: он ничего не ускоряет,
# но замедляет каждую вставку и занимает 324 МБ.
docker exec spy_postgres psql -U "$(docker exec spy_postgres printenv POSTGRES_USER)" \
  -d "$(docker exec spy_postgres printenv POSTGRES_DB)" \
  -c "drop index concurrently if exists ix_ads_content_fp"
echo "=== $(date -u +'%F %T UTC') готово ==="
