update ads u set started_at = s.started_at from ads s
where u.country=:'tgt' and u.started_at is null and s.country=:'cell'
  and s.library_id = u.library_id and s.started_at is not null;
