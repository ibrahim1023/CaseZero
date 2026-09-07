from psycopg import AsyncConnection

from casezero_retrieval.models import EvidenceSearchQuery, EvidenceSearchResult

_SEARCH_SQL = """
with search_input as (
    select %(investigation_id)s::uuid as investigation_id,
           %(case_id)s::uuid as case_id,
           plainto_tsquery('english'::regconfig, %(query_text)s::text) as tsquery,
           %(evidence_types)s::text[] as evidence_types,
           %(document_types)s::text[] as document_types,
           %(entity_ids)s::uuid[] as entity_ids,
           %(start_at)s::timestamptz as start_at,
           %(end_at)s::timestamptz as end_at
), components as (
    select evidence.id as evidence_id,
           ts_rank_cd(
               to_tsvector('english'::regconfig, coalesce(evidence.item->>'observation', '')),
               input.tsquery, 32)::numeric as fts_score,
           case when cardinality(input.entity_ids) = 0 then 0::numeric
                else (
                    select count(distinct entity_link.entity_id)::numeric
                    from public.entity_evidence_links entity_link
                    join public.investigation_entities entity
                      on entity.id = entity_link.entity_id
                     and entity.investigation_id = entity_link.investigation_id
                     and entity.case_id = entity_link.case_id
                    where entity_link.investigation_id = input.investigation_id
                      and entity_link.case_id = input.case_id
                      and entity_link.evidence_id = evidence.id
                      and entity_link.entity_id = any(input.entity_ids)
                ) / cardinality(input.entity_ids)
           end as entity_score,
           case when (input.start_at is not null or input.end_at is not null)
                     and exists (
                         select 1
                         from public.timeline_evidence_links timeline_link
                         join public.timeline_events event
                           on event.id = timeline_link.timeline_event_id
                          and event.investigation_id = timeline_link.investigation_id
                          and event.case_id = timeline_link.case_id
                         where timeline_link.investigation_id = input.investigation_id
                           and timeline_link.case_id = input.case_id
                           and timeline_link.evidence_id = evidence.id
                           and event.occurred_at is not null
                           and (input.start_at is null or event.occurred_at >= input.start_at)
                           and (input.end_at is null or event.occurred_at <= input.end_at)
                     ) then 1::numeric else 0::numeric
           end as time_score,
           case when cardinality(input.evidence_types) > 0
                      or cardinality(input.document_types) > 0
                then 1::numeric else 0::numeric
           end as type_score,
           cardinality(input.evidence_types) > 0 as has_evidence_type_filter,
           cardinality(input.document_types) > 0 as has_document_type_filter,
           cardinality(input.entity_ids) > 0 as has_entity_filter,
           (input.start_at is not null or input.end_at is not null) as has_time_filter
    from public.investigation_evidence membership
    join public.investigations investigation
      on investigation.id = membership.investigation_id
     and investigation.case_id = membership.case_id
    join public.evidence_items evidence
      on evidence.id = membership.evidence_id
     and evidence.case_id = membership.case_id
     and evidence.model_run_id is not distinct from membership.model_run_id
    join public.source_documents source
      on source.id = evidence.source_document_id
     and source.case_id = evidence.case_id
    left join public.semantic_unit_completions completion
      on completion.structural_unit_id = evidence.structural_unit_id
     and completion.model_run_id = evidence.model_run_id
    cross join search_input input
    where membership.investigation_id = input.investigation_id
      and membership.case_id = input.case_id
      and source.visibility = 'INVESTIGATION_EVIDENCE'
      and evidence.review_status in ('NOT_REQUIRED', 'ACCEPTED')
      and (
          completion.model_run_id is not null
          or (evidence.model_run_id is null
              and evidence.item->>'extraction_method' in ('DETERMINISTIC', 'HUMAN'))
      )
      and to_tsvector('english'::regconfig, coalesce(evidence.item->>'observation', ''))
          @@ input.tsquery
      and (cardinality(input.evidence_types) = 0
           or evidence.item->>'type' = any(input.evidence_types))
      and (cardinality(input.document_types) = 0
           or source.document_type = any(input.document_types))
), scored as (
    select evidence_id, fts_score, entity_score, time_score, type_score,
           round(0.55 * fts_score + 0.20 * entity_score
                 + 0.15 * time_score + 0.10 * type_score, 4) as total_score,
           array_remove(array[
               case when has_evidence_type_filter then 'evidence_type' end,
               case when has_document_type_filter then 'document_type' end,
               case when has_entity_filter then 'entity' end,
               case when has_time_filter then 'time' end
           ], null) as matched_filters
    from components
    where (not has_entity_filter or entity_score > 0)
      and (not has_time_filter or time_score > 0)
)
select evidence_id, fts_score, entity_score, time_score, type_score, total_score, matched_filters
from scored
order by total_score desc, fts_score desc, entity_score desc, time_score desc,
         type_score desc, evidence_id asc
limit %(limit)s
"""


class RetrievalRepository:
    def __init__(self, connection: AsyncConnection[tuple[object, ...]]) -> None:
        self._connection = connection

    async def search(self, query: EvidenceSearchQuery) -> tuple[EvidenceSearchResult, ...]:
        async with self._connection.cursor() as cursor:
            await cursor.execute("select current_user")
            if await cursor.fetchone() != ("casezero_blind",):
                raise PermissionError("retrieval requires a casezero_blind connection")
            await cursor.execute(
                _SEARCH_SQL,
                {
                    "investigation_id": query.investigation_id,
                    "case_id": query.case_id,
                    "query_text": query.query_text,
                    "evidence_types": [value.value for value in query.evidence_types],
                    "document_types": [value.value for value in query.document_types],
                    "entity_ids": list(query.entity_ids),
                    "start_at": query.start_at,
                    "end_at": query.end_at,
                    "limit": query.limit,
                },
            )
            rows = await cursor.fetchall()
        return tuple(_result_from_row(row, rank) for rank, row in enumerate(rows, start=1))


def _result_from_row(row: tuple[object, ...], rank: int) -> EvidenceSearchResult:
    evidence_id, fts, entity, time, type_score, total, matched_filters = row
    if not isinstance(matched_filters, list):
        raise TypeError("retrieval matched filters must be a PostgreSQL text array")
    return EvidenceSearchResult.model_validate(
        {
            "evidence_id": evidence_id,
            "rank": rank,
            "fts_score": fts,
            "entity_score": entity,
            "time_score": time,
            "type_score": type_score,
            "total_score": total,
            "matched_filters": tuple(matched_filters),
        }
    )
