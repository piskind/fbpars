#!/bin/bash
# Полнотекстовый индекс вместо триграммного.
#
# Триграммный (ix_ads_poisk_trgm, 5.4 ГБ, строился 1ч20м) задачу не решил:
# на слове testosterone он отдавал 178 773 кандидата при 8 364 настоящих
# совпадениях. Выборка этих кандидатов из таблицы на 18 ГБ читала ~7 ГБ с диска,
# запрос занимал 197 с — хуже, чем вообще без индекса (120 с).
#
# Полнотекстовый ищет по СЛОВАМ, а не по подстрокам, и вернёт ровно совпадения.
# Конфигурация 'simple' — без выделения основы: база многоязычная, и приводить
# английские окончания к основе, а остальные нет, значит получить разное
# поведение для разных языков.
#
# Плата: поиск по куску слова ('testoster') работать перестанет. Для дашборда,
# где ищут словами и фразами, это верный размен.
set -u
LOG=/root/fbpars/indeks_fts.log
P="docker exec -i spy_postgres psql -U spy -d spy -v ON_ERROR_STOP=1"

echo "=== $(date -u '+%F %T') строю полнотекстовый индекс ===" >> $LOG

# 4 МБ мало: битовая карта не помещалась и вырождалась в постраничную,
# из-за чего перепроверялось 437 767 лишних строк.
echo "alter system set work_mem = '64MB';" | $P >> $LOG 2>&1
echo "select pg_reload_conf();" | $P >> $LOG 2>&1
echo "$(date -u '+%H:%M:%S') work_mem поднят до 64MB" >> $LOG

echo "create index concurrently if not exists ix_ads_poisk_fts on ads using gin (
  to_tsvector('simple',
    coalesce(body,'') || ' ' || coalesce(title,'') || ' ' || coalesce(caption,'')
    || ' ' || coalesce(page_name,'') || ' ' || coalesce(display_url,''))
);" | $P >> $LOG 2>&1

echo "$(date -u '+%H:%M:%S') индекс построен" >> $LOG
echo "select pg_size_pretty(pg_relation_size('ix_ads_poisk_fts')) as razmer;" | $P >> $LOG 2>&1
echo "analyze ads;" | $P >> $LOG 2>&1
echo "=== $(date -u '+%F %T') готово ===" >> $LOG
