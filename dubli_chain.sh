#!/bin/bash
exec >>/root/fbpars/dubli.log 2>&1
echo "=== $(date -u +%F\ %T) цепочка дублей: старт ==="
docker exec fbpars-parser-worker-1 python /app/dubli_migraciya.py --shag zapolnit
docker exec fbpars-parser-worker-1 python /app/dubli_migraciya.py --shag pometit
docker exec fbpars-parser-worker-1 python /app/dubli_migraciya.py --shag otchet
echo "=== $(date -u +%F\ %T) цепочка дублей: готово ==="
