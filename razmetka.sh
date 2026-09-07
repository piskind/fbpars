#!/bin/bash
# Разметка повторов: в пределах ОДНОЙ страны. Тот же креатив в другом гео —
# законная отдельная карточка, лента фильтруется по стране.
set -u
LOG=/root/fbpars/razmetka.log
PSQL="docker exec -i spy_postgres psql -U spy -d spy -v ON_ERROR_STOP=1 -q -t -A"

echo "=== $(date -u '+%F %T') разметка повторов (внутри страны) ===" >> $LOG
echo "$(date -u '+%H:%M:%S') сбрасываю прежнюю разметку" >> $LOG
echo "update ads set is_dup = false where is_dup;" | $PSQL >> $LOG 2>&1

# Шестнадцатью порциями по первому символу отпечатка: одно окно на 5.6 млн строк
# не укладывалось в statement_timeout и откатывалось целиком.
for C in 0 1 2 3 4 5 6 7 8 9 a b c d e f; do
  echo "update ads set is_dup = true where id in (
          select id from (
            select id, row_number() over (partition by content_fp, country order by id) as nomer
            from ads where content_fp like '$C%'
          ) t where nomer > 1);" | $PSQL >> $LOG 2>&1
  echo "$(date -u '+%H:%M:%S') порция $C" >> $LOG
done

echo "$(date -u '+%H:%M:%S') ANALYZE" >> $LOG
echo "analyze ads;" | $PSQL >> $LOG 2>&1
echo "select 'ИТОГ: строк '||count(*)||', уникальных креативов в своём гео '||count(*) filter (where not is_dup)||', помечено повтором '||count(*) filter (where is_dup) from ads;" | $PSQL >> $LOG 2>&1
echo "=== $(date -u '+%F %T') разметка готова ===" >> $LOG
