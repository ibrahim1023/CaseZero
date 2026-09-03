create or replace function public.reject_lock_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  raise exception 'investigation locks are immutable' using errcode = 'P0001';
end
$$;

create or replace function public.protect_case_lifecycle()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if old.evidence_cutoff is distinct from new.evidence_cutoff
     and not (
       old.state = 'ACQUIRING'
       or (old.state = 'BLIND' and old.evidence_cutoff is null)
     ) then
    raise exception 'evidence cutoff is immutable after blind entry'
      using errcode = 'P0001';
  end if;

  if old.state <> new.state and not (
    (old.state = 'ACQUIRING' and new.state = 'BLIND')
    or (old.state = 'BLIND' and new.state = 'LOCKED')
    or (old.state = 'LOCKED' and new.state = 'REVEALED')
  ) then
    raise exception 'invalid case state transition' using errcode = 'P0001';
  end if;

  if new.state in ('LOCKED', 'REVEALED') and not exists (
    select 1 from public.investigation_locks lock_record
    where lock_record.case_id = new.id
  ) then
    raise exception 'locked case requires an investigation lock'
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

create or replace function public.require_unlocked_case_row()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  target_case_id uuid;
begin
  target_case_id = case when tg_op = 'DELETE' then old.case_id else new.case_id end;
  if not exists (
    select 1 from public.cases case_record
    where case_record.id = target_case_id
      and case_record.state in ('ACQUIRING', 'BLIND')
  ) then
    raise exception 'source inventory is immutable after lock'
      using errcode = 'P0001';
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end
$$;

create or replace function public.require_blind_case_row()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  target_case_id uuid;
begin
  target_case_id = case when tg_op = 'DELETE' then old.case_id else new.case_id end;
  if not exists (
    select 1 from public.cases case_record
    where case_record.id = target_case_id and case_record.state = 'BLIND'
  ) then
    raise exception 'processing state is immutable outside BLIND'
      using errcode = 'P0001';
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end
$$;

create or replace function public.require_blind_source_row()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  target_source_id uuid;
begin
  target_source_id = case
    when tg_op = 'DELETE' then old.source_document_id
    else new.source_document_id
  end;
  if not exists (
    select 1
    from public.source_documents source
    join public.cases case_record on case_record.id = source.case_id
    where source.id = target_source_id and case_record.state = 'BLIND'
  ) then
    raise exception 'processing state is immutable outside BLIND'
      using errcode = 'P0001';
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end
$$;

create or replace function public.require_blind_unit_completion()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  target_unit_id uuid;
begin
  target_unit_id = case
    when tg_op = 'DELETE' then old.structural_unit_id
    else new.structural_unit_id
  end;
  if not exists (
    select 1
    from public.structural_units unit
    join public.source_documents source on source.id = unit.source_document_id
    join public.cases case_record on case_record.id = source.case_id
    where unit.id = target_unit_id and case_record.state = 'BLIND'
  ) then
    raise exception 'semantic completion is immutable outside BLIND'
      using errcode = 'P0001';
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end
$$;

create or replace function public.require_blind_processing_skip()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  target_docket_id uuid;
begin
  target_docket_id = case
    when tg_op = 'DELETE' then old.docket_item_id
    else new.docket_item_id
  end;
  if not exists (
    select 1
    from public.docket_items docket
    join public.cases case_record on case_record.id = docket.case_id
    where docket.id = target_docket_id and case_record.state = 'BLIND'
  ) then
    raise exception 'processing skip is immutable outside BLIND'
      using errcode = 'P0001';
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end
$$;

create trigger investigation_locks_immutable
before update or delete on public.investigation_locks
for each row execute function public.reject_lock_mutation();

create trigger cases_lifecycle_guard
before update on public.cases
for each row execute function public.protect_case_lifecycle();

create trigger docket_items_lock_guard
before insert or update or delete on public.docket_items
for each row execute function public.require_unlocked_case_row();
create trigger source_documents_lock_guard
before insert or update or delete on public.source_documents
for each row execute function public.require_unlocked_case_row();

create trigger processing_runs_lock_guard
before insert or update or delete on public.processing_runs
for each row execute function public.require_blind_source_row();
create trigger derived_artifacts_lock_guard
before insert or update or delete on public.derived_artifacts
for each row execute function public.require_blind_source_row();
create trigger structural_units_lock_guard
before insert or update or delete on public.structural_units
for each row execute function public.require_blind_source_row();

create trigger model_runs_lock_guard
before insert or update or delete on public.model_runs
for each row execute function public.require_blind_case_row();
create trigger evidence_items_lock_guard
before insert or update or delete on public.evidence_items
for each row execute function public.require_blind_case_row();
create trigger claim_candidates_lock_guard
before insert or update or delete on public.claim_candidates
for each row execute function public.require_blind_case_row();
create trigger entity_candidates_lock_guard
before insert or update or delete on public.entity_candidates
for each row execute function public.require_blind_case_row();
create trigger timeline_candidates_lock_guard
before insert or update or delete on public.timeline_candidates
for each row execute function public.require_blind_case_row();
create trigger candidate_batch_completions_lock_guard
before insert or update or delete on public.candidate_batch_completions
for each row execute function public.require_blind_case_row();

create trigger semantic_unit_completions_lock_guard
before insert or update or delete on public.semantic_unit_completions
for each row execute function public.require_blind_unit_completion();
create trigger processing_skips_lock_guard
before insert or update or delete on public.processing_skips
for each row execute function public.require_blind_processing_skip();

revoke all on function public.reject_lock_mutation() from public, anon, authenticated;
revoke all on function public.protect_case_lifecycle() from public, anon, authenticated;
revoke all on function public.require_unlocked_case_row() from public, anon, authenticated;
revoke all on function public.require_blind_case_row() from public, anon, authenticated;
revoke all on function public.require_blind_source_row() from public, anon, authenticated;
revoke all on function public.require_blind_unit_completion() from public, anon, authenticated;
revoke all on function public.require_blind_processing_skip() from public, anon, authenticated;
