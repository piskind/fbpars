#!/bin/bash
# Таблица по ключам: ключ / сколько видел в библиотеке / сколько спарсилось.
# Повторы креатива НЕ учитываются нигде: считаются только уникальные (not is_dup).
# Прогон США, целевой день 21.08.2026 (окно 24.08 14:36 — 26.08 17:12).
#
# «Видел» берётся из журнала срезов, а он пишется В КОНЦЕ среза: крупные ключи
# упирались в таймаут задания, крео сохранить успевали, а строку журнала нет.
# Поэтому там, где журнал заведомо неполон (видел меньше, чем спарсилось —
# физически невозможно), пишем «не записано», а не ноль: иначе лучшие ключи
# выглядели бы пустыми. Таких 146 из 996.
set -u
VYHOD=/root/fbpars/klyuchi_us_21_08.csv

docker exec -i spy_postgres psql -q -U spy -d spy -v ON_ERROR_STOP=1 <<'SQL' > $VYHOD
\pset format csv
with okno as (
  select min(finished_at) - interval '30 minutes' as ot
  from slice_events where den = date '2026-08-21'),
den as (select timestamptz '2026-08-21 07:00+00' as ot,
               timestamptz '2026-08-22 07:00+00' as do),
videl as (
  select lower(slovo) as k, sum(vsego) as prosmotreno
  from slice_events where den = date '2026-08-21' and slovo is not null group by 1),
sparsil as (
  select lower(a.keyword) as k, count(*) as sohraneno
  from ads a, den, okno
  where a.country = 'US' and a.keyword is not null
    and a.first_seen_at >= okno.ot
    and a.started_at >= den.ot and a.started_at < den.do
    and not a.is_dup
  group by 1),
svod as (
  select v.k, v.prosmotreno, coalesce(s.sohraneno, 0) as sohraneno
  from videl v left join sparsil s on s.k = v.k)
select k                                    as "Ключ",
       case when prosmotreno < sohraneno
            then 'не записано'
            else prosmotreno::text end      as "Видел в библиотеке",
       sohraneno                            as "Спарсилось"
from svod
order by sohraneno desc, prosmotreno desc;
SQL

echo "строк: $(( $(wc -l < $VYHOD) - 1 ))"
