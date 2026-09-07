
drop table if exists dupes_to_delete;
create table dupes_to_delete as
select id from (
  select id, row_number() over (partition by library_id
                                order by first_seen_at asc, id asc) as rn
  from ads
) t where rn > 1;
create index ix_dtd_id on dupes_to_delete (id);
select count(*) as k_udaleniyu from dupes_to_delete;
