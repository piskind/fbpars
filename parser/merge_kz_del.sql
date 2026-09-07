with doomed as (
  select a.id from ads a
  where a.country=:'cell'
    and exists (select 1 from ads b where b.country='KZ' and b.library_id=a.library_id)
  limit 20000
), saved as (
  insert into ads_us_merge_backup select * from ads where id in (select id from doomed) returning id
)
delete from ads where id in (select id from saved);
