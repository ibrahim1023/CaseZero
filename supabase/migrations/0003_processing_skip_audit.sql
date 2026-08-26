create table public.processing_skips (
  id uuid primary key default gen_random_uuid(),
  docket_item_id uuid not null references public.docket_items(id) on delete restrict,
  status text not null check (status = 'SKIPPED_RIGHTS'),
  reason text not null,
  created_at timestamptz not null,
  unique (docket_item_id, status)
);

alter table public.processing_skips enable row level security;
alter table public.processing_skips force row level security;

create policy processor_skips on public.processing_skips
  for all to casezero_processor
  using (true)
  with check (true);

grant select, insert, update on public.processing_skips to casezero_processor;
