
-- Слияние ячеек США в один неймспейс US. Обратимо: исходная ячейка сохраняется
-- в source_cell, удалённые дубли — в ads_us_merge_backup.
alter table ads add column if not exists source_cell varchar(32);
create table if not exists ads_us_merge_backup (like ads including defaults);
create index if not exists ix_merge_bak_lib on ads_us_merge_backup (library_id);

do $mig$
declare
  cell text;
  n bigint;
  total_del bigint := 0;
  total_upd bigint := 0;
begin
  foreach cell in array array['usJ1','usaJUNE','US5','US6','US8'] loop

    -- 1) дата создания не должна потеряться: доливаем её выжившей строке
    update ads u set started_at = s.started_at
    from ads s
    where u.country = 'US' and u.started_at is null
      and s.country = cell and s.library_id = u.library_id
      and s.started_at is not null;

    -- 2) дубли уносим в бэкап и удаляем порциями
    loop
      with doomed as (
        select a.id from ads a
        where a.country = cell
          and exists (select 1 from ads b where b.country='US' and b.library_id=a.library_id)
        limit 50000
      ), saved as (
        insert into ads_us_merge_backup select * from ads where id in (select id from doomed)
        returning id
      )
      delete from ads where id in (select id from saved);
      get diagnostics n = row_count;
      total_del := total_del + n;
      exit when n = 0;
    end loop;

    -- 3) остальное переносим, помечая происхождение
    loop
      with moved as (
        select id from ads where country = cell limit 50000
      )
      update ads set country = 'US', source_cell = cell
      where id in (select id from moved);
      get diagnostics n = row_count;
      total_upd := total_upd + n;
      exit when n = 0;
    end loop;

    raise notice 'ячейка % готова: удалено дублей %, перенесено %', cell, total_del, total_upd;
  end loop;
  raise notice 'ИТОГО удалено % перенесено %', total_del, total_upd;
end
$mig$;
