select w, count(*) c from (
  select regexp_split_to_table(lower(coalesce(body,'') || ' ' || coalesce(title,'')), '[^a-z0-9]+') w
  from ads where country='usJ1' and started_at::date = date '2026-06-01'
) t
where length(w) between 4 and 20 and w !~ '^[0-9]+$'
group by w having count(*) >= 5
order by c desc limit 40000;
