create or replace function public.link_processable_source(
  target_case_id uuid,
  target_docket_item_id uuid,
  target_source_url text,
  target_expected_checksum text
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public
set timezone = 'UTC'
as $$
declare
  scoped_role text;
  case_state text;
  case_cutoff timestamptz;
  docket_disposition text;
  docket_checksum text;
  source_record public.source_documents%rowtype;
  existing_blob_path text;
begin
  scoped_role = current_setting('role', true);
  if scoped_role <> 'casezero_processor' then
    raise exception 'processable source linking requires processor role'
      using errcode = '42501';
  end if;

  select state, evidence_cutoff
  into case_state, case_cutoff
  from public.cases
  where id = target_case_id
  for update;
  if not found or case_state <> 'BLIND' or case_cutoff is null then
    raise exception 'case is not processable' using errcode = '42501';
  end if;

  select processing_disposition, expected_checksum
  into docket_disposition, docket_checksum
  from public.docket_items
  where id = target_docket_item_id and case_id = target_case_id
  for update;
  if not found
     or docket_disposition <> 'AI_ALLOWED'
     or docket_checksum is distinct from target_expected_checksum then
    raise exception 'docket item is not approved for processing'
      using errcode = '42501';
  end if;

  select *
  into source_record
  from public.source_documents
  where case_id = target_case_id and source_url = target_source_url
  for update;
  if not found or source_record.checksum <> target_expected_checksum then
    raise exception 'acquired source checksum does not match review'
      using errcode = 'P0001';
  end if;
  if not public.is_blind_metadata_eligible(
    source_record.title,
    source_record.document_type,
    source_record.visibility,
    source_record.published_at,
    case_cutoff,
    docket_disposition
  ) then
    raise exception 'acquired source is outside the blind boundary'
      using errcode = '42501';
  end if;

  insert into public.source_blobs (checksum, storage_path, byte_size)
  values (source_record.checksum, source_record.storage_path, 0)
  on conflict (checksum) do nothing;

  select storage_path
  into existing_blob_path
  from public.source_blobs
  where checksum = source_record.checksum;
  if existing_blob_path is distinct from source_record.storage_path then
    raise exception 'source blob path conflicts with checksum'
      using errcode = 'P0001';
  end if;

  update public.source_documents
  set docket_item_id = target_docket_item_id,
      blob_checksum = source_record.checksum
  where id = source_record.id;

  insert into public.access_audit_events (
    case_id, occurred_at, stage, actor_role, capability, operation,
    target_document_id, allowed, reason_code
  ) values (
    target_case_id, transaction_timestamp(), 'PROCESSING', 'PROCESSOR',
    'SUPABASE_DATABASE', 'WRITE', source_record.id, true,
    'ALLOWED_PROCESSING'
  );

  return source_record.id;
end
$$;

create or replace function public.protect_processor_docket_classification()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if current_setting('role', true) = 'casezero_processor' and (
    old.title is distinct from new.title
    or old.source_url is distinct from new.source_url
    or old.document_type is distinct from new.document_type
    or old.published_at is distinct from new.published_at
    or old.rights_status is distinct from new.rights_status
    or old.processing_disposition is distinct from new.processing_disposition
    or old.expected_checksum is distinct from new.expected_checksum
  ) then
    raise exception 'processor cannot change reviewed docket classification'
      using errcode = '42501';
  end if;
  return new;
end
$$;

create trigger docket_items_processor_classification_guard
before update on public.docket_items
for each row execute function public.protect_processor_docket_classification();

revoke all on function public.link_processable_source(uuid, uuid, text, text)
  from public, anon, authenticated, casezero_blind, casezero_eval;
grant execute on function public.link_processable_source(uuid, uuid, text, text)
  to casezero_processor;
revoke all on function public.protect_processor_docket_classification()
  from public, anon, authenticated;
