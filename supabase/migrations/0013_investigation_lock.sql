create or replace function public.lock_investigation(
  target_case_id uuid,
  snapshot jsonb,
  target_model_versions jsonb,
  target_prompt_versions jsonb,
  target_system_version text
) returns public.investigation_locks
language plpgsql
security definer
set search_path = pg_catalog, public
set timezone = 'UTC'
as $$
declare
  current_state text;
  current_cutoff timestamptz;
  evidence_projection jsonb;
  assessment_digest text;
  evidence_digest text;
  lock_time timestamptz;
  result public.investigation_locks%rowtype;
begin
  select state, evidence_cutoff
  into current_state, current_cutoff
  from public.cases
  where id = target_case_id
  for update;

  if not found then
    raise exception 'case does not exist' using errcode = 'P0002';
  end if;
  if current_state <> 'BLIND' or current_cutoff is null then
    raise exception 'case must be BLIND with an evidence cutoff'
      using errcode = 'P0001';
  end if;
  if snapshot is null
     or jsonb_typeof(snapshot) <> 'object'
     or length(snapshot->>'schema_version') = 0
     or length(snapshot->>'assessment_kind') = 0
     or not snapshot ? 'payload' then
    raise exception 'assessment snapshot is invalid' using errcode = '22023';
  end if;
  if target_model_versions is null
     or jsonb_typeof(target_model_versions) <> 'object'
     or target_model_versions = '{}'::jsonb then
    raise exception 'model versions are required' using errcode = '22023';
  end if;
  if target_prompt_versions is null
     or jsonb_typeof(target_prompt_versions) <> 'object'
     or target_prompt_versions = '{}'::jsonb then
    raise exception 'prompt versions are required' using errcode = '22023';
  end if;
  if target_system_version is null or length(target_system_version) = 0 then
    raise exception 'system version is required' using errcode = '22023';
  end if;
  if exists (
    select 1 from public.investigation_locks
    where case_id = target_case_id
  ) then
    raise exception 'investigation is already locked' using errcode = '23505';
  end if;

  select jsonb_agg(
    jsonb_build_object(
      'evidence_id', evidence.id,
      'item', evidence.item,
      'model_run_id', semantic_run.id,
      'prompt_hash', semantic_run.prompt_hash,
      'structural_unit_id', unit.id,
      'content_checksum', unit.content_checksum,
      'source_document_id', source.id,
      'source_checksum', source.checksum
    ) order by evidence.id
  )
  into evidence_projection
  from public.semantic_unit_completions completion
  join public.model_runs semantic_run
    on semantic_run.id = completion.model_run_id
  join public.evidence_items evidence
    on evidence.model_run_id = completion.model_run_id
   and evidence.structural_unit_id = completion.structural_unit_id
  join public.structural_units unit
    on unit.id = evidence.structural_unit_id
  join public.source_documents source
    on source.id = evidence.source_document_id
  where evidence.case_id = target_case_id;

  if evidence_projection is null or jsonb_array_length(evidence_projection) = 0 then
    raise exception 'active evidence is required' using errcode = 'P0001';
  end if;

  assessment_digest = encode(
    extensions.digest(convert_to(snapshot::text, 'UTF8'), 'sha256'),
    'hex'
  );
  evidence_digest = encode(
    extensions.digest(convert_to(evidence_projection::text, 'UTF8'), 'sha256'),
    'hex'
  );
  lock_time = transaction_timestamp();

  insert into public.investigation_locks (
    case_id, assessment_snapshot, assessment_hash, evidence_set_hash,
    hash_algorithm, model_versions, prompt_versions, system_version, locked_at
  ) values (
    target_case_id, snapshot, assessment_digest, evidence_digest,
    'postgres-jsonb-text-v1', target_model_versions, target_prompt_versions,
    target_system_version, lock_time
  ) returning * into result;

  update public.cases
  set state = 'LOCKED'
  where id = target_case_id;

  insert into public.access_audit_events (
    case_id, occurred_at, stage, actor_role, capability, operation,
    allowed, reason_code
  ) values (
    target_case_id, lock_time, 'BLIND', 'BLIND', 'LOCK', 'LOCK',
    true, 'LOCK_CREATED'
  );

  return result;
end
$$;

revoke all on function public.lock_investigation(uuid, jsonb, jsonb, jsonb, text)
  from public, anon, authenticated, casezero_processor, casezero_eval;
grant execute on function public.lock_investigation(uuid, jsonb, jsonb, jsonb, text)
  to casezero_blind;
