#!/bin/bash
# Пересборка отпечатков с именем файла и повторная разметка.
#
# Отпечаток без файла склеивал разные креативы: из 4.4 млн скрытых настоящими
# повторами были только 237 065, остальные 4.17 млн отличались картинкой.
# Правило теперь: повтор = рекламодатель + текст + тот же файл, в пределах страны.
set -u
LOG=/root/fbpars/fp3.log
P="docker exec -i spy_postgres psql -U spy -d spy -v ON_ERROR_STOP=1 -q -t -A"

echo "=== $(date -u '+%F %T') пересборка отпечатков (с файлом) ===" >> $LOG

FP="md5(coalesce(page_id,'') || btrim(left(coalesce(body,''),400), E' \t\n\r\f\v') || btrim(left(coalesce(title,''),200), E' \t\n\r\f\v') || regexp_replace(split_part(coalesce((image_urls)[1], (video_urls)[1], ''), '?', 1), '^.*/', ''))"

MAXID=$(echo "select coalesce(max(id),0) from ads" | $P)
SHAG=250000
OT=0
while [ "$OT" -le "$MAXID" ]; do
  DO=$((OT + SHAG))
  echo "update ads set content_fp = $FP where id > $OT and id <= $DO;" | $P >> $LOG 2>&1
  echo "$(date -u '+%H:%M:%S') id $OT..$DO" >> $LOG
  OT=$DO
done
echo "$(date -u '+%H:%M:%S') отпечатки пересобраны" >> $LOG

echo "$(date -u '+%H:%M:%S') снимаю прежнюю разметку" >> $LOG
echo "update ads set is_dup = false where is_dup;" | $P >> $LOG 2>&1

for C in 0 1 2 3 4 5 6 7 8 9 a b c d e f; do
  echo "update ads set is_dup = true where id in (
          select id from (
            select id, row_number() over (partition by content_fp, country order by id) as n
            from ads where content_fp like '$C%'
          ) t where n > 1);" | $P >> $LOG 2>&1
  echo "$(date -u '+%H:%M:%S') порция $C" >> $LOG
done

echo "analyze ads;" | $P >> $LOG 2>&1
echo "select 'ИТОГ: всего '||count(*)||', показываем '||count(*) filter (where not is_dup)||', скрыто повторов '||count(*) filter (where is_dup) from ads;" | $P >> $LOG 2>&1
echo "=== $(date -u '+%F %T') готово ===" >> $LOG
