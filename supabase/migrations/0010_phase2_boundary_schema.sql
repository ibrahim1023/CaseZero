create extension if not exists pgcrypto with schema extensions;

alter table public.cases
  add column evidence_cutoff timestamptz;

create table public.investigation_locks (
  case_id uuid primary key references public.cases(id) on delete restrict,
  assessment_snapshot jsonb not null check (
    jsonb_typeof(assessment_snapshot) = 'object'
    and length(assessment_snapshot->>'schema_version') > 0
    and length(assessment_snapshot->>'assessment_kind') > 0
    and assessment_snapshot ? 'payload'
  ),
  assessment_hash text not null check (assessment_hash ~ '^[0-9a-f]{64}$'),
  evidence_set_hash text not null check (evidence_set_hash ~ '^[0-9a-f]{64}$'),
  hash_algorithm text not null check (hash_algorithm = 'postgres-jsonb-text-v1'),
  model_versions jsonb not null check (
    jsonb_typeof(model_versions) = 'object' and model_versions <> '{}'::jsonb
  ),
  prompt_versions jsonb not null check (
    jsonb_typeof(prompt_versions) = 'object' and prompt_versions <> '{}'::jsonb
  ),
  system_version text not null check (length(system_version) > 0),
  locked_at timestamptz not null
);

create table public.access_audit_events (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.cases(id) on delete restrict,
  occurred_at timestamptz not null,
  stage text not null check (stage in ('ACQUISITION', 'PROCESSING', 'BLIND', 'EVALUATION')),
  actor_role text not null check (actor_role in ('ACQUISITION', 'PROCESSOR', 'BLIND', 'EVALUATION')),
  capability text not null check (capability in (
    'NTSB_ACQUISITION', 'CONTEXT_DEV', 'WEB_SEARCH', 'RAW_STORAGE',
    'SUPABASE_DATABASE', 'MODEL_INFERENCE', 'EVIDENCE_READ', 'LOCK'
  )),
  operation text not null check (operation in ('READ', 'WRITE', 'NETWORK', 'LOCK', 'DENY')),
  target_document_id uuid,
  network_host text check (
    network_host is null or network_host ~
      '^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$'
  ),
  allowed boolean not null,
  reason_code text not null check (reason_code in (
    'ALLOWED_ACQUISITION', 'ALLOWED_PROCESSING', 'ALLOWED_EVIDENCE_READ',
    'ALLOWED_MODEL_HOST', 'ALLOWED_DATABASE', 'BLOCKED_CAPABILITY',
    'BLOCKED_DOCUMENT', 'EVALUATION_LOCK_REQUIRED', 'LOCK_CREATED', 'ENTERED_BLIND'
  )),
  check ((operation = 'NETWORK') = (network_host is not null)),
  check (
    (operation = 'DENY' and not allowed)
    or (operation <> 'DENY' and allowed)
  ),
  check (
    (actor_role = 'ACQUISITION' and stage = 'ACQUISITION')
    or (actor_role = 'PROCESSOR' and stage = 'PROCESSING')
    or (actor_role = 'BLIND' and stage = 'BLIND')
    or (actor_role = 'EVALUATION' and stage = 'EVALUATION')
  )
);

create index access_audit_events_case_time_idx
  on public.access_audit_events(case_id, occurred_at, id);

alter table public.investigation_locks enable row level security;
alter table public.investigation_locks force row level security;
alter table public.access_audit_events enable row level security;
alter table public.access_audit_events force row level security;

revoke all privileges on public.investigation_locks from anon, authenticated;
revoke all privileges on public.access_audit_events from anon, authenticated;
