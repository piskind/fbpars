#!/bin/bash
# Триграммный индекс под поиск в клиентском дашборде.
# Поиск делает ILIKE '%слово%' по шести текстовым полям на 7.4 млн строк —
# обычный btree тут бесполезен, нужен GIN по триграммам. Замер до правки:
# счётчик 120 с, лента 44 с, из-за чего фронт показывал размер страницы (40).
#
# Индексируем ОДНО склеенное выражение, а не шесть полей по отдельности:
# один индекс вместо шести, и запрос переписывается на то же выражение.
set -u
LOG=/root/fbpars/indeks_poiska.log
P="docker exec -i spy_postgres psql -U spy -d spy -v ON_ERROR_STOP=1"

echo "=== $(date -u '+%F %T') строю индекс поиска ===" >> $LOG
echo "create extension if not exists pg_trgm;" | $P >> $LOG 2>&1
echo "$(date -u '+%H:%M:%S') pg_trgm готов" >> $LOG

echo "create index concurrently if not exists ix_ads_poisk_trgm on ads using gin (
  (coalesce(body,'') || ' ' || coalesce(title,'') || ' ' || coalesce(caption,'')
   || ' ' || coalesce(page_name,'') || ' ' || coalesce(display_url,'')) gin_trgm_ops
);" | $P >> $LOG 2>&1

echo "$(date -u '+%H:%M:%S') индекс построен" >> $LOG
echo "select pg_size_pretty(pg_relation_size('ix_ads_poisk_trgm')) as razmer_indeksa;" | $P >> $LOG 2>&1
echo "analyze ads;" | $P >> $LOG 2>&1
echo "=== $(date -u '+%F %T') готово ===" >> $LOG
