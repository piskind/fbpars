with moved as (select id from ads where country=:'cell' limit 20000)
update ads set country='US', source_cell=:'cell' where id in (select id from moved);
