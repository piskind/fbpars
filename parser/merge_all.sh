#!/bin/sh
D=/root/fbpars/parser
LOG=/root/merge_all.log
: > $LOG
PS="docker exec -i spy_postgres psql -U spy -d spy -tA"
run() { docker exec -i spy_postgres psql -U spy -d spy -tA -v cell="$1" -v tgt="$2" < "$3" 2>&1; }

CELLS=$($PS < $D/list_cells.sql)
for CELL in $CELLS; do
  TGT=$(printf "%s" "$CELL" | tr -cd "[:alpha:]" | cut -c1-2 | tr "[:lower:]" "[:upper:]")
  case "$TGT" in ??) : ;; *) echo "пропуск $CELL" >> $LOG; continue ;; esac
  echo "=== $CELL -> $TGT $(date +%H:%M) ===" >> $LOG
  run "$CELL" "$TGT" $D/merge_gen_bf.sql >> $LOG
  while : ; do
    OUT=$(run "$CELL" "$TGT" $D/merge_gen_del.sql)
    case "$OUT" in *"DELETE 0"*) break ;; *ERROR*) echo "  del err" >> $LOG; sleep 5 ;; esac
  done
  while : ; do
    OUT=$(run "$CELL" "$TGT" $D/merge_gen_upd.sql)
    case "$OUT" in *"UPDATE 0"*) break ;; *ERROR*) echo "  upd err" >> $LOG; sleep 5 ;; esac
  done
  echo "$CELL готов" >> $LOG
done
echo "ВСЕ_МЕТКИ_СЛИТЫ $(date +%H:%M)" >> $LOG
