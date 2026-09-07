#!/bin/bash
# Ждёт, пока очередь опустеет, и собирает итоговую таблицу по ключам.
# Нужно потому, что сборка таблицы имеет смысл только когда по каждому ключу
# отработал active-срез — иначе в ней будут те же ложные нули.
set -u
LOG=/root/fbpars/tablica.log
exec >>"$LOG" 2>&1
echo "=== $(date -u +'%F %T UTC') жду опустошения очереди ==="

for i in $(seq 1 2880); do          # до 24 часов, проверка раз в 30 с
  N=$(docker exec spy_redis redis-cli llen rq:queue:parse 2>/dev/null | tr -d '\r')
  R=$(docker exec fbpars-parser-worker-1 python -c "
from rq.registry import StartedJobRegistry
from app.queue import parse_queue
print(len(StartedJobRegistry(queue=parse_queue()).get_job_ids()))" 2>/dev/null | tail -1)
  if [ "${N:-1}" = "0" ] && [ "${R:-1}" = "0" ]; then
    echo "$(date -u +'%F %T') очередь пуста и работа закончена"
    break
  fi
  if [ $((i % 20)) -eq 0 ]; then
    echo "$(date -u +'%F %T') в очереди ${N:-?}, в работе ${R:-?}"
  fi
  sleep 30
done

echo "$(date -u +'%F %T') собираю таблицу"
docker cp /tmp/tablica.sql spy_postgres:/tmp/tablica.sql
docker exec spy_postgres sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -f /tmp/tablica.sql' \
  > /root/fbpars/kluchi_us_21_08.csv 2>/dev/null
sed -i '/^Output format is csv/d' /root/fbpars/kluchi_us_21_08.csv
echo "$(date -u +'%F %T') готово: $(wc -l < /root/fbpars/kluchi_us_21_08.csv) строк"
echo "=== $(date -u +'%F %T UTC') конец ==="
