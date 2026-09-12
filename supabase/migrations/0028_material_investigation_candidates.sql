create or replace function public.phase3_candidate_is_material(target_kind text, target_candidate jsonb)
returns boolean language plpgsql immutable set search_path=pg_catalog,public as $$
declare candidate_text text;
begin
 candidate_text := case target_kind
  when 'claim' then target_candidate->>'text'
  when 'entity' then target_candidate->>'proposed_canonical_name'
  when 'timeline' then target_candidate->>'description'
 end;
 if candidate_text is null or btrim(candidate_text)='' then return false; end if;
 return candidate_text !~* '(^|[^[:alnum:]_])row[[:space:]]+[0-9]+([^[:alnum:]_]|$).*(^|[^[:alnum:]_])column[[:space:]]+[0-9]+([^[:alnum:]_]|$)'
  and candidate_text !~* '^(numerical[[:space:]]+)?observation([^[:alnum:]_]|$).*(recorded|documented|row|column|cell)'
  and candidate_text !~* '^[[:space:]]*[+-]?[0-9]+([.][0-9]+)?[[:space:]]*([A-Za-z%]+)?[[:space:]]*$';
end $$;

revoke all on function public.phase3_candidate_is_material(text,jsonb)
from public,anon,authenticated,casezero_blind,casezero_eval,casezero_processor;

create or replace function public.create_investigation(target_case_id uuid, target_configuration jsonb)
returns public.investigations language plpgsql security definer set search_path = pg_catalog, public as $$
declare result public.investigations; snapshot jsonb; snapshot_hash text; config_hash text;
begin
  perform public.phase3_assert_blind();
  perform 1 from public.cases where id = target_case_id and state = 'BLIND'
    and evidence_cutoff is not null for update;
  if not found then raise exception 'case must be blind with cutoff' using errcode = '42501'; end if;
  if target_configuration is null or jsonb_typeof(target_configuration) <> 'object' or target_configuration = '{}' then
    raise exception 'configuration is required' using errcode = '22023';
  end if;
  select jsonb_agg(jsonb_build_object('evidence_id', e.id, 'model_run_id', e.model_run_id,
    'item', e.item, 'review_status', e.review_status, 'source_checksum', s.checksum) order by e.id)
  into snapshot
  from public.evidence_items e
  join public.semantic_unit_completions c on c.structural_unit_id = e.structural_unit_id and c.model_run_id = e.model_run_id
  join public.source_documents s on s.id = e.source_document_id and s.case_id = e.case_id
  join public.structural_units u on u.id = e.structural_unit_id and u.source_document_id = s.id
  join public.model_runs m on m.id = e.model_run_id and m.case_id = e.case_id and m.status = 'SUCCEEDED'
  join public.docket_items d on d.id = s.docket_item_id and d.case_id = s.case_id
  join public.cases cr on cr.id = s.case_id
  where e.case_id = target_case_id and e.review_status in ('NOT_REQUIRED', 'ACCEPTED')
    and public.is_blind_metadata_eligible(s.title, s.document_type, s.visibility, s.published_at, cr.evidence_cutoff, d.processing_disposition);
  if snapshot is null then raise exception 'eligible reviewed evidence required' using errcode = '55000'; end if;
  snapshot_hash := encode(extensions.digest(convert_to(snapshot::text, 'UTF8'), 'sha256'), 'hex');
  config_hash := encode(extensions.digest(convert_to(jsonb_build_object(
    'algorithm', 'postgres-investigation-config-v1', 'configuration', target_configuration,
    'evidence_set_hash', snapshot_hash)::text, 'UTF8'), 'sha256'), 'hex');
  select * into result from public.investigations where case_id = target_case_id
    and configuration_hash = config_hash order by created_at desc limit 1;
  if found then return result; end if;
  insert into public.investigations(case_id, status, current_stage, configuration_hash, configuration,
    evidence_set_hash, model_versions, prompt_versions, created_at)
  values (target_case_id, 'PENDING', 'PROMOTE_TIMELINE', config_hash, target_configuration,
    snapshot_hash, target_configuration->'model_versions',
    target_configuration->'prompt_versions', clock_timestamp())
  returning * into result;
  insert into public.investigation_evidence(investigation_id, case_id, evidence_id, model_run_id)
  select result.id, target_case_id, (entry->>'evidence_id')::uuid, (entry->>'model_run_id')::uuid
    from jsonb_array_elements(snapshot) entry;
  insert into public.investigation_candidates(investigation_id, case_id, candidate_id, kind, model_run_id)
  select result.id, target_case_id, c.id, c.kind, c.model_run_id from (
    select id, case_id, model_run_id, evidence_ids, candidate, 'claim'::text kind from public.claim_candidates
    union all select id, case_id, model_run_id, evidence_ids, candidate, 'entity' from public.entity_candidates
    union all select id, case_id, model_run_id, evidence_ids, candidate, 'timeline' from public.timeline_candidates
  ) c where c.case_id = target_case_id and cardinality(c.evidence_ids) > 0
    and public.phase3_candidate_is_material(c.kind,c.candidate)
    and exists (select 1 from public.candidate_batch_completions b where b.case_id = target_case_id and b.model_run_id = c.model_run_id)
    and not exists (select 1 from unnest(c.evidence_ids) eid where not exists (
      select 1 from public.investigation_evidence p where p.investigation_id = result.id and p.evidence_id = eid
    ));
  perform public.append_investigation_event(result.id, 'INVESTIGATION_STARTED', 'investigation', result.id,
    jsonb_build_object('kind','INVESTIGATION_STARTED', 'configuration_hash',config_hash,
      'current_stage','PROMOTE_TIMELINE','status','PENDING'));
  perform public.phase3_enqueue_stage(result.id, 'PROMOTE_TIMELINE');
  select * into result from public.investigations where id = result.id;
  return result;
end
$$;
