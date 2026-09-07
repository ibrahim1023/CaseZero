create or replace function public.append_investigation_event(
  target_investigation_id uuid, target_event_type text, target_type text,
  target_id uuid, target_payload jsonb, target_model_run_id uuid default null
) returns public.investigation_events language plpgsql security definer set search_path = pg_catalog, public as $$
declare inv public.investigations; result public.investigation_events; envelope jsonb;
begin
  perform public.phase3_assert_blind();
  select * into inv from public.investigations where id = target_investigation_id for update;
  if not found or inv.status not in ('PENDING', 'RUNNING') then
    raise exception 'investigation is not writable' using errcode = '55000';
  end if;
  perform 1 from public.cases where id = inv.case_id and state = 'BLIND' and evidence_cutoff is not null for share;
  if not found then raise exception 'case is not blind' using errcode = '42501'; end if;
  if jsonb_typeof(target_payload) is distinct from 'object' then
    raise exception 'event payload must be an object' using errcode = '22023';
  end if;
  target_payload := jsonb_build_object('schema_version', '1') || target_payload;
  if target_payload->'schema_version' is distinct from '"1"'::jsonb then
    raise exception 'unsupported event payload version' using errcode = '22023';
  end if;
  if target_payload->>'kind' is distinct from target_event_type then
    raise exception 'event payload type mismatch' using errcode = '22023';
  end if;
  if target_model_run_id is not null and not exists (
    select 1 from public.model_runs where id = target_model_run_id and case_id = inv.case_id
  ) then raise exception 'event model run mismatch' using errcode = '23514'; end if;
  envelope := jsonb_build_object(
    'previous_hash', inv.event_head_hash, 'investigation_id', inv.id, 'case_id', inv.case_id,
    'sequence', inv.event_sequence + 1, 'event_type', target_event_type,
    'target_type', target_type, 'target_id', target_id, 'payload', target_payload,
    'model_run_id', target_model_run_id
  );
  insert into public.investigation_events(
    investigation_id, case_id, sequence, event_type, target_type, target_id, payload,
    model_run_id, previous_event_hash, event_hash
  ) values (
    inv.id, inv.case_id, inv.event_sequence + 1, target_event_type, target_type, target_id,
    target_payload, target_model_run_id, inv.event_head_hash,
    encode(extensions.digest(convert_to(envelope::text, 'UTF8'), 'sha256'), 'hex')
  ) returning * into result;
  update public.investigations set event_sequence = result.sequence, event_head_hash = result.event_hash
    where id = inv.id;
  return result;
end
$$;

alter table public.hypothesis_tests
  add column result_evidence_ids uuid[] not null default '{}';
