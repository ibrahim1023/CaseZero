create table public.investigations (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.cases(id) on delete restrict,
  status text not null check (status in ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
  current_stage text not null check (current_stage in (
    'PROMOTE_TIMELINE', 'RESOLVE_ENTITIES', 'PROMOTE_CLAIMS',
    'GENERATE_HYPOTHESES', 'SEARCH_SUPPORT', 'SEARCH_CONTRADICTIONS',
    'DESIGN_FALSIFICATION_TESTS', 'EXECUTE_FALSIFICATION_TESTS',
    'REVISE_CONFIDENCE', 'VERIFY_REPLAY', 'COMPLETE'
  )),
  configuration_hash text not null check (configuration_hash ~ '^[0-9a-f]{64}$'),
  model_versions jsonb not null check (jsonb_typeof(model_versions) = 'object' and model_versions <> '{}'::jsonb),
  prompt_versions jsonb not null check (jsonb_typeof(prompt_versions) = 'object' and prompt_versions <> '{}'::jsonb),
  failure_code text check (failure_code in (
    'PROVIDER_EXHAUSTED', 'SCHEMA_INVALID', 'REFERENCE_INVALID',
    'DIVERSITY_INVALID', 'LEASE_EXPIRED', 'REPLAY_MISMATCH', 'PERSISTENCE_FAILED'
  )),
  created_at timestamptz not null,
  started_at timestamptz,
  completed_at timestamptz
);
create unique index investigations_one_active_config_idx
  on public.investigations(case_id, configuration_hash)
  where status in ('PENDING', 'RUNNING');

create table public.claims (
  id uuid primary key,
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  text text not null check (length(text) > 0),
  status text not null check (status in ('OBSERVED', 'INFERRED', 'DISPUTED', 'UNKNOWN')),
  confidence numeric(5,4) not null check (confidence between 0 and 1),
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  created_at timestamptz not null
);
create table public.claim_candidate_links (
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  claim_id uuid not null references public.claims(id) on delete restrict,
  candidate_id uuid not null references public.claim_candidates(id) on delete restrict,
  primary key (claim_id, candidate_id)
);
create table public.claim_evidence_links (
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  claim_id uuid not null references public.claims(id) on delete restrict,
  evidence_id uuid not null references public.evidence_items(id) on delete restrict,
  polarity text not null check (polarity in ('SUPPORTING', 'CONTRADICTING')),
  primary key (claim_id, evidence_id, polarity)
);

create table public.investigation_entities (
  id uuid primary key,
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  type text not null check (length(type) > 0),
  canonical_name text not null check (length(canonical_name) > 0),
  aliases text[] not null default '{}',
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  created_at timestamptz not null
);
create table public.entity_candidate_links (
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  entity_id uuid not null references public.investigation_entities(id) on delete restrict,
  candidate_id uuid not null references public.entity_candidates(id) on delete restrict,
  primary key (entity_id, candidate_id)
);
create table public.entity_evidence_links (
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  entity_id uuid not null references public.investigation_entities(id) on delete restrict,
  evidence_id uuid not null references public.evidence_items(id) on delete restrict,
  primary key (entity_id, evidence_id)
);

create table public.timeline_events (
  id uuid primary key,
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  occurred_at timestamptz,
  time_precision text not null check (time_precision in ('EXACT', 'APPROXIMATE', 'RELATIVE', 'UNKNOWN')),
  description text not null check (length(description) > 0),
  confidence numeric(5,4) not null check (confidence between 0 and 1),
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  created_at timestamptz not null
);
create table public.timeline_candidate_links (
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  timeline_event_id uuid not null references public.timeline_events(id) on delete restrict,
  candidate_id uuid not null references public.timeline_candidates(id) on delete restrict,
  primary key (timeline_event_id, candidate_id)
);
create table public.timeline_evidence_links (
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  timeline_event_id uuid not null references public.timeline_events(id) on delete restrict,
  evidence_id uuid not null references public.evidence_items(id) on delete restrict,
  primary key (timeline_event_id, evidence_id)
);

create table public.hypotheses (
  id uuid primary key,
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  title text not null check (length(title) > 0),
  description text not null check (length(description) > 0),
  initial_confidence numeric(5,4) not null check (initial_confidence between 0.05 and 0.85),
  current_confidence numeric(5,4) not null check (current_confidence between 0 and 1),
  status text not null check (status in ('ACTIVE', 'WEAKENED', 'REJECTED', 'LEADING')),
  distinguishing_prediction text not null check (length(distinguishing_prediction) > 0),
  weakening_evidence text not null check (length(weakening_evidence) > 0),
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  created_at timestamptz not null
);
create table public.hypothesis_claim_links (
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  hypothesis_id uuid not null references public.hypotheses(id) on delete restrict,
  claim_id uuid not null references public.claims(id) on delete restrict,
  polarity text not null check (polarity in ('SUPPORTING', 'CONTRADICTING')),
  primary key (hypothesis_id, claim_id, polarity)
);
create table public.unresolved_questions (
  id uuid primary key,
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  hypothesis_id uuid not null references public.hypotheses(id) on delete restrict,
  text text not null check (length(text) > 0),
  created_at timestamptz not null
);
create table public.hypothesis_critiques (
  id uuid primary key,
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  hypothesis_id uuid not null references public.hypotheses(id) on delete restrict,
  strongest_contradiction_id uuid references public.evidence_items(id) on delete restrict,
  missing_evidence text[] not null default '{}',
  alternative_explanation text,
  critique_confidence numeric(5,4) not null check (critique_confidence between 0 and 1),
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  created_at timestamptz not null
);
create table public.hypothesis_tests (
  id uuid primary key,
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  hypothesis_id uuid not null references public.hypotheses(id) on delete restrict,
  critique_id uuid not null references public.hypothesis_critiques(id) on delete restrict,
  job_id uuid not null,
  type text not null check (type in ('EVIDENCE_PRESENCE', 'TEMPORAL_CONSISTENCY', 'CLAIM_CONTRADICTION', 'SEMANTIC_COMPARISON')),
  expected_observation text not null check (length(expected_observation) > 0),
  strength text not null check (strength in ('LOW', 'MEDIUM', 'HIGH')),
  execution_kind text not null check (execution_kind in ('DETERMINISTIC', 'AI')),
  parameters jsonb not null check (jsonb_typeof(parameters) = 'object' and length(parameters->>'schema_version') > 0),
  status text not null check (status in ('PENDING', 'SUCCEEDED', 'FAILED')),
  outcome text check (outcome in ('CONTRADICTED', 'SURVIVED', 'EXPECTED_EVIDENCE_MISSING', 'INCONCLUSIVE')),
  evidence_ids uuid[] not null default '{}',
  claim_ids uuid[] not null default '{}',
  model_run_id uuid references public.model_runs(id) on delete restrict,
  created_at timestamptz not null,
  completed_at timestamptz
);
create table public.confidence_revisions (
  id uuid primary key,
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  hypothesis_id uuid not null references public.hypotheses(id) on delete restrict,
  before numeric(5,4) not null check (before between 0 and 1),
  delta numeric(6,4) not null check (delta between -1 and 1),
  after numeric(5,4) not null check (after between 0.05 and 0.95),
  rule_version text not null check (rule_version = 'weighted-delta-v1'),
  rationale text not null check (length(rationale) > 0),
  created_at timestamptz not null
);
create table public.confidence_revision_test_deltas (
  investigation_id uuid not null references public.investigations(id) on delete restrict,
  case_id uuid not null references public.cases(id) on delete restrict,
  revision_id uuid not null references public.confidence_revisions(id) on delete restrict,
  test_id uuid not null references public.hypothesis_tests(id) on delete restrict,
  ordinal integer not null check (ordinal >= 0),
  delta numeric(5,4) not null check (delta between -0.35 and 0.06),
  primary key (revision_id, test_id),
  unique (revision_id, ordinal)
);

create or replace function public.phase3_blind_can_read(target_case_id uuid)
returns boolean language sql stable security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1 from public.cases
    where id = target_case_id and state in ('BLIND', 'LOCKED')
  )
$$;
create or replace function public.phase3_blind_can_write(target_case_id uuid)
returns boolean language sql stable security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1 from public.cases
    where id = target_case_id and state = 'BLIND'
  )
$$;
create or replace function public.phase3_eval_can_read(target_case_id uuid)
returns boolean language sql stable security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1 from public.cases case_record
    join public.investigation_locks lock_record on lock_record.case_id = case_record.id
    where case_record.id = target_case_id and case_record.state in ('LOCKED', 'REVEALED')
  )
$$;

revoke all on function public.phase3_blind_can_read(uuid) from public, anon, authenticated;
revoke all on function public.phase3_blind_can_write(uuid) from public, anon, authenticated;
revoke all on function public.phase3_eval_can_read(uuid) from public, anon, authenticated;
grant execute on function public.phase3_blind_can_read(uuid) to casezero_blind;
grant execute on function public.phase3_blind_can_write(uuid) to casezero_blind;
grant execute on function public.phase3_eval_can_read(uuid) to casezero_eval;

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'investigations', 'claims', 'claim_candidate_links', 'claim_evidence_links',
    'investigation_entities', 'entity_candidate_links', 'entity_evidence_links',
    'timeline_events', 'timeline_candidate_links', 'timeline_evidence_links',
    'hypotheses', 'hypothesis_claim_links', 'unresolved_questions',
    'hypothesis_critiques', 'hypothesis_tests', 'confidence_revisions',
    'confidence_revision_test_deltas'
  ] loop
    execute format('alter table public.%I enable row level security', table_name);
    execute format('alter table public.%I force row level security', table_name);
    execute format('revoke all privileges on public.%I from anon, authenticated', table_name);
    execute format('grant select, insert, update on public.%I to casezero_blind', table_name);
    execute format('grant select on public.%I to casezero_eval', table_name);
    execute format(
      'create policy %I on public.%I for select to casezero_blind using (public.phase3_blind_can_read(case_id))',
      table_name || '_blind_select', table_name
    );
    execute format(
      'create policy %I on public.%I for insert to casezero_blind with check (public.phase3_blind_can_write(case_id))',
      table_name || '_blind_insert', table_name
    );
    execute format(
      'create policy %I on public.%I for update to casezero_blind using (public.phase3_blind_can_write(case_id)) with check (public.phase3_blind_can_write(case_id))',
      table_name || '_blind_update', table_name
    );
    execute format(
      'create policy %I on public.%I for select to casezero_eval using (public.phase3_eval_can_read(case_id))',
      table_name || '_eval_select', table_name
    );
    execute format(
      'create trigger %I before insert or update or delete on public.%I for each row execute function public.require_blind_case_row()',
      table_name || '_lock_guard', table_name
    );
  end loop;
end
$$;
