create or replace function public.enter_blind(
  target_case_id uuid,
  target_cutoff timestamptz
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public
set timezone = 'UTC'
as $$
declare
  current_state text;
  current_cutoff timestamptz;
begin
  if target_cutoff is null then
    raise exception 'evidence cutoff is required' using errcode = '22004';
  end if;

  select state, evidence_cutoff
  into current_state, current_cutoff
  from public.cases
  where id = target_case_id
  for update;

  if not found then
    raise exception 'case does not exist' using errcode = 'P0002';
  end if;

  if current_state = 'ACQUIRING' then
    update public.cases
    set state = 'BLIND', evidence_cutoff = target_cutoff
    where id = target_case_id;
  elsif current_state = 'BLIND' and current_cutoff is null then
    update public.cases
    set evidence_cutoff = target_cutoff
    where id = target_case_id;
  elsif current_state <> 'BLIND' or current_cutoff <> target_cutoff then
    raise exception 'case state or cutoff conflicts with BLIND transition'
      using errcode = 'P0001';
  end if;

  insert into public.access_audit_events (
    case_id, occurred_at, stage, actor_role, capability, operation,
    allowed, reason_code
  ) values (
    target_case_id, transaction_timestamp(), 'ACQUISITION', 'ACQUISITION',
    'SUPABASE_DATABASE', 'WRITE', true, 'ENTERED_BLIND'
  );

  return target_case_id;
end
$$;

revoke all on function public.enter_blind(uuid, timestamptz)
  from public, anon, authenticated, casezero_blind, casezero_eval, casezero_processor;
