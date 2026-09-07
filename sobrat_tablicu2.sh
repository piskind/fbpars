#!/bin/bash
# Ждёт окончания ВОЛНЫ ЗАМЕРА (не всей очереди — она уйдёт ещё на двое суток)
# и собирает итоговую таблицу по ключам.
set -u
LOG=/root/fbpars/tablica.log
exec >>"$LOG" 2>&1
echo "=== $(date -u +'%F %T UTC') жду окончания замеров ==="

for i in $(seq 1 720); do          # до 6 часов
  OST=$(docker exec fbpars-parser-worker-1 python -c "
from rq.registry import StartedJobRegistry
from app.queue import parse_queue
q = parse_queue(); conn = q.connection
ids = [j.decode() if isinstance(j, bytes) else j for j in conn.lrange(q.key, 0, -1)]
st = StartedJobRegistry(queue=q).get_job_ids()
print(len([i for i in ids if i.startswith('zamer_')]) + len([i for i in st if i.startswith('zamer_')]))
" 2>/dev/null | tail -1)
  if [ "${OST:-1}" = "0" ]; then
    echo "$(date -u +'%F %T') замеры закончены"
    break
  fi
  if [ $((i % 10)) -eq 0 ]; then
    echo "$(date -u +'%F %T') осталось замеров: ${OST:-?}"
  fi
  sleep 30
done

echo "$(date -u +'%F %T') собираю таблицу"
docker cp /tmp/tablica.sql spy_postgres:/tmp/tablica.sql
docker exec spy_postgres sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -f /tmp/tablica.sql' \
  > /root/fbpars/kluchi_us_21_08.csv 2>/dev/null
sed -i '/^Output format is csv/d' /root/fbpars/kluchi_us_21_08.csv
echo "$(date -u +'%F %T') готово: $(wc -l < /root/fbpars/kluchi_us_21_08.csv) строк"

# воркеры замера больше не нужны — освобождаем память основному сбору
docker rm -f $(docker ps -q --filter name=fbpars-zamer) >/dev/null 2>&1
echo "$(date -u +'%F %T') временные воркеры замера сняты"
echo "=== $(date -u +'%F %T UTC') конец ==="
