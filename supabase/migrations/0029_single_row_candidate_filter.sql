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
  and candidate_text !~* '^(data|value|observation)([^[:alnum:]_]|$).*(^|[^[:alnum:]_])row[[:space:]]+[0-9]+([^[:alnum:]_]|$)'
  and candidate_text !~* '^[[:space:]]*[+-]?[0-9]+([.][0-9]+)?[[:space:]]*([A-Za-z%]+)?[[:space:]]*$';
end $$;
