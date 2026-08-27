alter table public.source_blobs enable row level security;
alter table public.source_blobs force row level security;
revoke all on public.source_blobs from anon, authenticated;
