create table public.casezero_environment (
  singleton boolean primary key default true check (singleton),
  name text not null check (name in ('development', 'staging', 'production'))
);

insert into public.casezero_environment (singleton, name)
values (true, 'development')
on conflict (singleton) do nothing;

alter table public.casezero_environment enable row level security;
alter table public.casezero_environment force row level security;
revoke all on public.casezero_environment from anon, authenticated;

insert into storage.buckets (id, name, public)
values
  ('casezero-sources', 'casezero-sources', false),
  ('casezero-derived', 'casezero-derived', false)
on conflict (id) do update set public = false;
