create or replace function public.append_access_audit_event(
  target_event_id uuid,
  target_case_id uuid,
  target_stage text,
  target_actor_role text,
  target_capability text,
  target_operation text,
  target_document_id uuid,
  target_network_host text,
  target_allowed boolean,
  target_reason_code text
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public
set timezone = 'UTC'
as $$
declare
  scoped_role text;
begin
  scoped_role = current_setting('role', true);
  if not (
    (scoped_role = 'casezero_processor'
      and target_stage = 'PROCESSING' and target_actor_role = 'PROCESSOR')
    or (scoped_role = 'casezero_blind'
      and target_stage = 'BLIND' and target_actor_role = 'BLIND')
    or (scoped_role = 'casezero_eval'
      and target_stage = 'EVALUATION' and target_actor_role = 'EVALUATION')
  ) then
    raise exception 'audit actor does not match scoped runtime role'
      using errcode = '42501';
  end if;

  insert into public.access_audit_events (
    id, case_id, occurred_at, stage, actor_role, capability, operation,
    target_document_id, network_host, allowed, reason_code
  ) values (
    target_event_id, target_case_id, transaction_timestamp(), target_stage,
    target_actor_role, target_capability, target_operation, target_document_id,
    target_network_host, target_allowed, target_reason_code
  );
  return target_event_id;
end
$$;

create or replace function public.reject_access_audit_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  raise exception 'access audit events are append-only' using errcode = 'P0001';
end
$$;

create trigger access_audit_events_immutable
before update or delete on public.access_audit_events
for each row execute function public.reject_access_audit_mutation();

revoke all privileges on public.access_audit_events
  from casezero_processor, casezero_blind, casezero_eval;
revoke all on function public.append_access_audit_event(
  uuid, uuid, text, text, text, text, uuid, text, boolean, text
) from public, anon, authenticated;
grant execute on function public.append_access_audit_event(
  uuid, uuid, text, text, text, text, uuid, text, boolean, text
) to casezero_processor, casezero_blind, casezero_eval;
revoke all on function public.reject_access_audit_mutation()
  from public, anon, authenticated;
