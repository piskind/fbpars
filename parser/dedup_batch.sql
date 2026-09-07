
with doomed as (
  delete from dupes_to_delete
   where id in (select id from dupes_to_delete limit 50000)
  returning id
), saved as (
  insert into ads_dupes_backup select * from ads where id in (select id from doomed)
  returning id
)
delete from ads where id in (select id from saved);
