do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'casezero_blind') then
    create role casezero_blind nologin nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'casezero_eval') then
    create role casezero_eval nologin nobypassrls;
  end if;
end
$$;

create table public.cases (
  id uuid primary key default gen_random_uuid(),
  ntsb_number text not null unique,
  title text not null,
  event_date timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  state text not null check (state in ('ACQUIRING', 'BLIND', 'LOCKED', 'REVEALED')),
  created_at timestamptz not null default now()
);

create table public.source_documents (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.cases(id) on delete restrict,
  title text not null,
  source_url text not null,
  published_at timestamptz,
  evidence_date timestamptz,
  retrieved_at timestamptz not null,
  document_type text not null,
  visibility text not null check (
    visibility in ('INVESTIGATION_EVIDENCE', 'OFFICIAL_ANALYSIS', 'FINAL_FINDING')
  ),
  checksum text not null check (checksum ~ '^[0-9a-f]{64}$'),
  storage_path text not null unique,
  created_at timestamptz not null default now(),
  unique (case_id, source_url)
);

create index source_documents_case_id_idx on public.source_documents(case_id);
create index source_documents_visibility_idx on public.source_documents(case_id, visibility);

alter table public.cases enable row level security;
alter table public.source_documents enable row level security;
alter table public.cases force row level security;
alter table public.source_documents force row level security;

revoke all on public.cases from anon, authenticated;
revoke all on public.source_documents from anon, authenticated;

grant casezero_blind, casezero_eval to postgres;
grant usage on schema public to casezero_blind, casezero_eval;
grant select on public.cases to casezero_blind, casezero_eval;
grant select on public.source_documents to casezero_blind, casezero_eval;

create policy blind_cases on public.cases
  for select
  to casezero_blind
  using (state in ('BLIND', 'LOCKED'));

create policy blind_evidence_only on public.source_documents
  for select
  to casezero_blind
  using (visibility = 'INVESTIGATION_EVIDENCE');

create policy evaluation_cases on public.cases
  for select
  to casezero_eval
  using (state in ('LOCKED', 'REVEALED'));

create policy evaluation_sources on public.source_documents
  for select
  to casezero_eval
  using (
    exists (
      select 1
      from public.cases
      where cases.id = source_documents.case_id
        and cases.state in ('LOCKED', 'REVEALED')
    )
  );
