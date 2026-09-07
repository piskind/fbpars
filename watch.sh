#!/bin/bash
# fbpars watchdog: пишет статус в лог и шлёт в Telegram (0 токенов LLM).
cd /root/fbpars || exit 0
COUNTS=$(docker compose exec -T postgres psql -U spy -d spy -tA -c \
  "SELECT string_agg(country||' '||cnt, ' · ' ORDER BY country) FROM (SELECT country,count(*) cnt FROM ads WHERE country IN ('US','CO','PE') GROUP BY country) t;" 2>/dev/null)
RL=$(docker compose logs --since 10m parser-worker 2>&1 | grep -c 1675004)
PAG=$(docker compose logs --since 3m parser-worker 2>&1 | grep -c "curl] page=")
TS=$(date '+%H:%M')
echo "$TS | ${COUNTS:-нет данных} | rl10m=$RL | pages3m=$PAG" >> /root/fbpars/watch.log

TOKEN="8764007414:AAE4NXl1vP-b31g0K-v4icSmNJoeOujzlOw"
CHAT="1128222093"
send(){ curl -s "https://api.telegram.org/bot$TOKEN/sendMessage" -d "chat_id=$CHAT" --data-urlencode "text=$1" >/dev/null; }

# Немедленный алерт при возврате лимита или простое воркера
if [ "$RL" -gt 15 ]; then
  send "⚠️ fbpars: вернулся rate-limit ($RL за 10м), IP греется. Сбор: ${COUNTS:-?}"
elif [ "$PAG" -eq 0 ] && [ "$RL" -eq 0 ]; then
  send "⚠️ fbpars: воркер не листает (простой?). Сбор: ${COUNTS:-?}"
fi

# Прогресс раз в ~30 мин (каждый 3-й запуск при cron */10)
N=$(( $(cat /root/fbpars/.watchn 2>/dev/null || echo 0) + 1 )); echo $N > /root/fbpars/.watchn
if [ $(( N % 3 )) -eq 0 ]; then send "📊 fbpars: ${COUNTS:-нет данных} | rl10m=$RL"; fi
