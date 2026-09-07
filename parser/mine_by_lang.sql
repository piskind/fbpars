
-- Намываем слова из ВСЕЙ базы, по каждому языку отдельно.
-- Выборка на язык ограничена: 150к объявлений статистически более чем достаточно,
-- а полный проход по 6 млн строк на каждый язык не нужен.
copy (
  with vyborka as (
    select language, body, title from (
      select language, body, title,
             row_number() over (partition by language order by id desc) as rn
      from ads
      where language is not null
        and (body is not null or title is not null)
    ) t where rn <= 150000
  ),
  slova as (
    select language,
           regexp_split_to_table(
             lower(coalesce(body,'') || ' ' || coalesce(title,'')),
             '[^[:alnum:]а-яёА-ЯЁ]+'
           ) as w
    from vyborka
  )
  select language, w, count(*) as c
  from slova
  where length(w) between 4 and 20
    and w !~ '^[0-9]+$'
  group by 1, 2
  having count(*) >= 20
  order by language, c desc
) to '/tmp/mined_by_lang.csv' with (format csv);
