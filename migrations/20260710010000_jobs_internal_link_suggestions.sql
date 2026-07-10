alter table public.jobs
  add column if not exists internal_link_suggestions jsonb default '[]'::jsonb;

update public.jobs
set internal_link_suggestions = '[]'::jsonb
where internal_link_suggestions is null;
