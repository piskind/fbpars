#!/bin/bash
# Сторож прогона. Следит за тем, на чём сбор ломался: блокировки фейсбука,
# застой очереди, срезы с нулём. Пишет строку в минуту.
#
# Три беды, каждая из которых выглядит как «просто мало собралось»:
#   • код 1675004 — фейсбук отдаёт 114 байт пустоты вместо карточек;
#   • застой — очередь не пуста, а новых крео нет;
#   • срезы с нулём — надо отличать пустой ключ от придушенного.
set -u
LOG=/root/fbpars/storozh.log
PSQL="docker exec -i spy_postgres psql -U spy -d spy -t -A -F| -q"

echo "=== $(date -u '+%F %T') сторож поднят ===" >> $LOG
PRED_KREO=""
ZASTOY=0

while true; do
  SVODKA=$(echo "select r.id, r.status,
                   (select count(*) from ads where first_seen_at > now() - interval '1 hour'),
                   (select count(*) filter (where collected_count>0) from chunk_progress where last_run_id=r.id),
                   (select count(*) from chunk_progress where last_run_id=r.id)
                 from parser_runs r order by r.id desc limit 1" | $PSQL 2>/dev/null)

  ID=$(echo "$SVODKA" | cut -d'|' -f1)
  STATUS=$(echo "$SVODKA" | cut -d'|' -f2)
  KREO=$(echo "$SVODKA" | cut -d'|' -f3)
  S_REZ=$(echo "$SVODKA" | cut -d'|' -f4)
  VSEGO=$(echo "$SVODKA" | cut -d'|' -f5)

  OCHERED=$(docker exec spy_parser python -c "
from redis import Redis; from rq import Queue
from rq.registry import StartedJobRegistry, FailedJobRegistry
import os
r=Redis.from_url(os.getenv('REDIS_URL'))
print(f\"{Queue('parse',connection=r).count}|{len(StartedJobRegistry('parse',connection=r))}|{len(FailedJobRegistry('parse',connection=r))}\")
" 2>/dev/null)
  V_OCH=$(echo "$OCHERED" | cut -d'|' -f1)
  V_RAB=$(echo "$OCHERED" | cut -d'|' -f2)
  UPALO=$(echo "$OCHERED" | cut -d'|' -f3)

  OGR=$(docker logs --since 70s spy_parser 2>&1 | grep -c 1675004)

  echo "$(date -u '+%d.%m %H:%M') | прогон #$ID $STATUS | очередь ${V_OCH:-?}, в работе ${V_RAB:-?}, упало ${UPALO:-?} | срезов с результатом $S_REZ/$VSEGO | крео за час $KREO | ограничений FB $OGR" >> $LOG

  if [ "${OGR:-0}" -gt 5 ]; then
    echo "   ВНИМАНИЕ: фейсбук ограничивает ($OGR за минуту) — проверить ротацию прокси" >> $LOG
  fi

  if [ "$(( ${V_OCH:-0} + ${V_RAB:-0} ))" -gt 0 ]; then
    if [ "$KREO" = "$PRED_KREO" ]; then
      ZASTOY=$((ZASTOY + 1))
      [ "$ZASTOY" -ge 5 ] && echo "   ВНИМАНИЕ: застой $ZASTOY минут без новых крео при непустой очереди" >> $LOG
    else
      ZASTOY=0
    fi
  fi
  PRED_KREO="$KREO"

  sleep 60
done
