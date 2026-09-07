select country from ads where country !~ '^[A-Z]{2}$' group by 1 order by count(*) desc;
