#!/usr/bin/env bash
# Хардненинг перед сдачей. Запускать на VPS из любого места.
set -euo pipefail
cd "$(dirname "$0")/.."   # в корень проекта (docker-compose.yml + .env)

: "${NEW_PG_PASSWORD:?Задай NEW_PG_PASSWORD}"
: "${NEW_ADMIN_PASSWORD:?Задай NEW_ADMIN_PASSWORD}"

# Спецсимволы ломают URL, SQL и shell-подстановку
case "$NEW_PG_PASSWORD$NEW_ADMIN_PASSWORD" in
  *[\'\$\`\\@/]* )
    echo "ОШИБКА: убери из паролей символы ' \$ \` \\ @ /"
    exit 1
    ;;
esac

echo "=== 1. .env: POSTGRES_PASSWORD + DATABASE_URL ==="
# Формат URL подтверждён: postgresql+asyncpg://spy:<pass>@postgres:5432/spy
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${NEW_PG_PASSWORD}|" .env
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://spy:${NEW_PG_PASSWORD}@postgres:5432/spy|" .env
echo ".env обновлён"

echo "=== 2. ALTER USER в работающем postgres ==="
# ALTER USER персистится в volume — POSTGRES_PASSWORD применяется только при первой инициализации
docker compose exec -T postgres psql -U spy -d spy -c \
  "ALTER USER spy PASSWORD '${NEW_PG_PASSWORD}';"

echo "=== 3. Пересоздаём api + parser (restart не перечитывает .env, нужен up -d) ==="
# postgres не трогаем — данные в volume, пароль уже сменён через ALTER USER
docker compose up -d api parser

echo "=== 4. Смена bcrypt-хэша пароля admin ==="
# Колонка — login (models_proxy.py:171); hash_password возвращает str, не bytes
NEW_HASH=$(docker compose exec -T api python3 -c \
  "from app.security import hash_password; print(hash_password('${NEW_ADMIN_PASSWORD}'))")
docker compose exec -T postgres psql -U spy -d spy -c \
  "UPDATE admin_users SET password_hash = '${NEW_HASH}' WHERE login = 'admin';"

echo ""
echo "Готово. Проверь:"
echo "  curl -s -X POST http://localhost:8000/api/auth/login \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"login\":\"admin\",\"password\":\"'\"'\"'\${NEW_ADMIN_PASSWORD}'\"'\"'\"}' | python3 -m json.tool"
