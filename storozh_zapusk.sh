#!/bin/bash
# Держит сторожа живым: docker exec умирает вместе с родительской сессией,
# поэтому перезапускаем его в цикле.
exec >>/root/fbpars/storozh.log 2>&1
while true; do
  docker exec fbpars-parser-worker-2 python /app/storozh2.py
  echo "$(date -u +%F\ %T) сторож завершился, поднимаю заново через 60 с"
  sleep 60
done
