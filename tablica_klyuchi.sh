#!/bin/bash
# Таблица по ключам для заказчика: ключ / сколько парсер видел в библиотеке /
# сколько сохранилось. Цифры берём ИЗ БАЗЫ, а не из счётчика срезов: счётчик
# nayde_no заполнялся не на всех путях, и по 157 ключам показывал ноль там, где
# в базе тысячи строк (fitness: счётчик 0, в базе 8229 за 21.08).
set -u
VYHOD=/root/fbpars/klyuchi_us_21_08_v2.csv

docker exec -i spy_postgres psql -U spy -d spy -v ON_ERROR_STOP=1 <<'SQL' > $VYHOD
\pset format csv
\pset tuples_only off
with den as (
  select timestamptz '2026-08-21 07:00+00' as ot, timestamptz '2026-08-22 07:00+00' as do
),
srezy as (
  select lower(slovo) as klyuch,
         sum(vsego)  as prosmotreno,
         count(*)    as srezov
  from slice_events, den
  where den = date '2026-08-21' and slovo is not null
  group by lower(slovo)
),
sohraneno as (
  select lower(a.keyword) as klyuch,
         count(*) filter (where a.started_at >= den.ot and a.started_at < den.do) as za_den,
         count(*) filter (where a.started_at >= den.ot and a.started_at < den.do
                            and not a.is_dup)                                     as unikalnyh,
         count(*)                                                                 as vsego_po_klyuchu
  from ads a, den
  where a.country = 'US' and a.keyword is not null
  group by lower(a.keyword)
)
select coalesce(s.klyuch, z.klyuch)              as "Ключ",
       coalesce(z.prosmotreno, 0)                as "Карточек просмотрено в библиотеке",
       coalesce(s.za_den, 0)                     as "Сохранено крео за 21 августа",
       coalesce(s.unikalnyh, 0)                  as "Из них уникальных креативов",
       coalesce(z.srezov, 0)                     as "Срезов"
from sohraneno s
full join srezy z on z.klyuch = s.klyuch
order by 3 desc, 2 desc;
SQL

echo "готово: $(wc -l < $VYHOD) строк"
