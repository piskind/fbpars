#!/bin/bash
# Дожидается конца расчёта отпечатков и сразу помечает повторы креатива.
# Отдельным скриптом, потому что расчёт идёт больше десяти минут, а пометка должна
# пойти сразу за ним — иначе лента ещё сутки показывает дубли.
set -u
LOG=/root/fbpars/dubli.log
exec >>"$LOG" 2>&1
echo "=== $(date -u +'%F %T UTC') жду конца расчёта отпечатков ==="

for i in $(seq 1 240); do          # до 2 часов
  if ! ps -eo cmd | grep -q "[d]ubli_migraciya.py --shag zapolnit"; then
    break
  fi
  sleep 30
done

echo "$(date -u +'%F %T') расчёт завершён, помечаю повторы"
docker exec fbpars-parser-worker-1 python /app/dubli_migraciya.py --shag pometit
echo "$(date -u +'%F %T') --- итог ---"
docker exec fbpars-parser-worker-1 python /app/dubli_migraciya.py --shag otchet
echo "=== $(date -u +'%F %T UTC') готово ==="
