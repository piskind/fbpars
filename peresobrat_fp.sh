#!/bin/bash
# Пересборка отпечатков креатива по новой формуле (без имени медиафайла)
# и повторная разметка повторов. Порциями по id — одним запросом на 7.4 млн
# строк база уже вставала колом (25 сессий на Lock: transactionid, вставки
# блокировались на 9 минут).
set -u
LOG=/root/fbpars/fp2.log
PSQL="docker exec -i spy_postgres psql -U spy -d spy -v ON_ERROR_STOP=1 -q -t -A"

echo "=== $(date -u '+%F %T') пересборка отпечатков ===" >> $LOG

FP="md5(coalesce(page_id,'') || case when (btrim(left(coalesce(body,''),400), E' \t\n\r\f\v') <> '' or btrim(left(coalesce(title,''),200), E' \t\n\r\f\v') <> '') then btrim(left(coalesce(body,''),400), E' \t\n\r\f\v') || btrim(left(coalesce(title,''),200), E' \t\n\r\f\v') else regexp_replace(split_part(coalesce((image_urls)[1], (video_urls)[1], ''), '?', 1), '^.*/', '') end)"

MAXID=$(echo "select coalesce(max(id),0) from ads" | $PSQL)
echo "$(date -u '+%H:%M:%S') строк до max(id)=$MAXID" >> $LOG

SHAG=200000
OT=0
while [ "$OT" -le "$MAXID" ]; do
  DO=$((OT + SHAG))
  N=$(echo "update ads set content_fp = $FP where id > $OT and id <= $DO;" | $PSQL 2>>$LOG | tail -1)
  echo "$(date -u '+%H:%M:%S') id $OT..$DO обновлено" >> $LOG
  OT=$DO
done
echo "$(date -u '+%H:%M:%S') отпечатки пересобраны" >> $LOG

# Разметка повторов: в каждой группе одинаковых отпечатков оставляем самую
# раннюю строку, остальные помечаем. Шестнадцатью порциями по первому символу
# отпечатка — одно окно на 5.6 млн строк не укладывалось в statement_timeout
# и откатывалось целиком.
echo "$(date -u '+%H:%M:%S') сбрасываю прежнюю разметку" >> $LOG
echo "update ads set is_dup = false where is_dup;" | $PSQL >> $LOG 2>&1

for C in 0 1 2 3 4 5 6 7 8 9 a b c d e f; do
  echo "update ads set is_dup = true where id in (
          select id from (
            select id, row_number() over (partition by content_fp order by id) as nomer
            from ads where content_fp like '$C%'
          ) t where nomer > 1);" | $PSQL >> $LOG 2>&1
  echo "$(date -u '+%H:%M:%S') порция $C размечена" >> $LOG
done

echo "$(date -u '+%H:%M:%S') ANALYZE" >> $LOG
echo "analyze ads;" | $PSQL >> $LOG 2>&1

echo "select 'ИТОГ: всего '||count(*)||', уникальных креативов '||count(distinct content_fp)||', помечено повтором '||count(*) filter (where is_dup) from ads;" | $PSQL >> $LOG 2>&1
echo "=== $(date -u '+%F %T') готово ===" >> $LOG
