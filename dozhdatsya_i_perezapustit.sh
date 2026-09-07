#!/bin/bash
# Ждёт закрытия прогона #290 и перезапускает spy_parser, чтобы координатор подхватил
# новый (умный) план дневного сбора.
#
# Зачем скриптом, а не руками: правка coordinator.py применяется только при рестарте
# spy_parser, а рестарт с незакрытым прогоном оставит его в статусе 'running' навсегда —
# после старта подхватываются только прогоны в статусе 'triggered', и API больше не даст
# запустить новый сбор («Парсер уже запущен»).
set -u
LOG=/root/fbpars/dozhdatsya.log
exec >>"$LOG" 2>&1
echo "=== $(date -u +'%F %T UTC') старт ожидания закрытия прогона 290 ==="

psql_() { docker exec spy_postgres sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB -tAc \"$1\"" 2>/dev/null | tr -d ' \r'; }

for i in $(seq 1 720); do          # до 12 часов, проверка раз в минуту
  ST=$(psql_ "select status from parser_runs where id=290")
  OCH=$(docker exec spy_redis redis-cli --raw lrange rq:queue:parse 0 -1 2>/dev/null | grep -c '^day_' || echo 0)
  if [ "$ST" != "running" ]; then
    echo "$(date -u +'%F %T') прогон 290 закрыт со статусом '$ST'"
    break
  fi
  if [ "$OCH" = "0" ] && [ $((i % 10)) -eq 0 ]; then
    echo "$(date -u +'%F %T') срезов прогона в очереди 0, статус всё ещё '$ST' (жду монитор)"
  fi
  sleep 60
done

ST=$(psql_ "select status from parser_runs where id=290")
if [ "$ST" = "running" ]; then
  # Монитор не закрыл прогон за 12 часов (его джобы могли исчезнуть по result_ttl).
  # Закрываем сами: иначе он навсегда заблокирует запуск новых сборов.
  echo "$(date -u +'%F %T') прогон 290 всё ещё 'running' — закрываю принудительно"
  psql_ "update parser_runs set status='done', finished_at=now() where id=290"
fi

echo "$(date -u +'%F %T') перезапускаю spy_parser (подхват нового плана)"
docker restart spy_parser
sleep 20
docker logs --tail 5 spy_parser
echo "=== $(date -u +'%F %T UTC') готово ==="
