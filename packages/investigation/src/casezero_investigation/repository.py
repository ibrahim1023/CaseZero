from uuid import UUID

from casezero_evidence import (
    ClaimCandidate,
    EntityCandidate,
    EvidenceItem,
    SourceDocument,
    TimelineCandidate,
)
from casezero_evidence.models import StrictModel
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, JsonValue

from casezero_investigation.events import InvestigationEvent
from casezero_investigation.jobs import (
    FailureCode,
    InvestigationConfig,
    InvestigationJob,
    ModelRequestAttempt,
    ValidationIssue,
)
from casezero_investigation.models import Investigation, InvestigationStage
from casezero_investigation.replay import InvestigationProjection


def _record[T: BaseModel](model: type[T], value: object) -> T:
    if not isinstance(value, dict):
        raise TypeError("database returned an invalid record")
    return model.model_validate(
        {key: value[key] for key in model.model_fields if key in value}, strict=False
    )


class CandidateBatch(StrictModel):
    claims: tuple[ClaimCandidate, ...] = ()
    entities: tuple[EntityCandidate, ...] = ()
    timeline: tuple[TimelineCandidate, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.claims or self.entities or self.timeline)


class InvestigationRepository:
    def __init__(self, connection: AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def create(self, case_id: UUID, configuration: dict[str, JsonValue]) -> Investigation:
        row = await (await self.connection.execute(
            "select to_jsonb(i) from public.create_investigation(%s,%s) i",
            (case_id, Jsonb(configuration)),
        )).fetchone()
        if row is None:
            raise ValueError("investigation was not created")
        return _record(Investigation, row[0])

    async def get(self, investigation_id: UUID) -> Investigation:
        row = await (await self.connection.execute(
            "select to_jsonb(i) from public.investigations i where id=%s", (investigation_id,),
        )).fetchone()
        if row is None:
            raise LookupError("investigation not accessible")
        return _record(Investigation, row[0])

    async def configuration(self, investigation_id: UUID) -> InvestigationConfig:
        row = await (await self.connection.execute(
            "select configuration from public.investigations where id=%s", (investigation_id,),
        )).fetchone()
        if row is None:
            raise LookupError("investigation not accessible")
        return InvestigationConfig.model_validate(row[0], strict=False)

    async def candidate_batch(self, job: InvestigationJob) -> CandidateBatch:
        kind = {
            InvestigationStage.PROMOTE_TIMELINE: "timeline",
            InvestigationStage.RESOLVE_ENTITIES: "entity",
            InvestigationStage.PROMOTE_CLAIMS: "claim",
        }[job.stage]
        offset = 0 if job.work_key == "case" else int(job.work_key) * 3
        if offset < 0:
            raise ValueError("invalid candidate batch")
        rows = await (await self.connection.execute(
            "with batch as (select * from public.investigation_candidates "
            "where investigation_id=%s and kind=%s order by candidate_id offset %s limit 3) "
            "select p.candidate_id, coalesce(c.candidate,e.candidate,t.candidate) from batch p "
            "left join public.claim_candidates c on p.kind='claim' and c.id=p.candidate_id "
            "and c.case_id=p.case_id and c.model_run_id=p.model_run_id "
            "left join public.entity_candidates e on p.kind='entity' and e.id=p.candidate_id "
            "and e.case_id=p.case_id and e.model_run_id=p.model_run_id "
            "left join public.timeline_candidates t on p.kind='timeline' and t.id=p.candidate_id "
            "and t.case_id=p.case_id and t.model_run_id=p.model_run_id order by p.candidate_id",
            (job.investigation_id, kind, offset),
        )).fetchall()
        if any(row[1] is None for row in rows) or (not rows and job.work_key != "case"):
            raise ValueError("pinned candidate batch unavailable")
        if rows and job.work_key == "case":
            raise ValueError("nonempty candidate set requires a batch job")
        if kind == "claim":
            return CandidateBatch(claims=tuple(ClaimCandidate.model_validate(row[1], strict=False) for row in rows))
        if kind == "entity":
            return CandidateBatch(entities=tuple(EntityCandidate.model_validate(row[1], strict=False) for row in rows))
        return CandidateBatch(timeline=tuple(TimelineCandidate.model_validate(row[1], strict=False) for row in rows))

    async def source_metadata(self, investigation_id: UUID, document_id: UUID) -> SourceDocument:
        row = await (await self.connection.execute(
            "select to_jsonb(s) from public.source_documents s where s.id=%s and exists ("
            "select 1 from public.investigation_evidence p join public.evidence_items e "
            "on e.id=p.evidence_id and e.case_id=p.case_id "
            "where p.investigation_id=%s and e.source_document_id=s.id and e.case_id=s.case_id)",
            (document_id, investigation_id),
        )).fetchone()
        if row is None:
            raise PermissionError("source not accessible")
        return _record(SourceDocument, row[0])

    async def claim(self, investigation_id: UUID, worker_id: str) -> InvestigationJob | None:
        row = await (await self.connection.execute(
            "select to_jsonb(j) from public.claim_investigation_job(%s,%s) j",
            (investigation_id, worker_id),
        )).fetchone()
        return _record(InvestigationJob, row[0]) if row else None

    async def reserve_request(self, job_id: UUID, worker_id: str, attempt: int) -> ModelRequestAttempt:
        row = await (await self.connection.execute(
            "select to_jsonb(r) from public.reserve_model_request(%s,%s,%s) r",
            (job_id, worker_id, attempt),
        )).fetchone()
        if row is None:
            raise ValueError("model request was not reserved")
        return _record(ModelRequestAttempt, row[0])

    async def requests(self, job_id: UUID) -> tuple[ModelRequestAttempt, ...]:
        rows = await (await self.connection.execute(
            "select to_jsonb(r) from public.model_request_attempts r where job_id=%s order by request_ordinal",
            (job_id,),
        )).fetchall()
        return tuple(_record(ModelRequestAttempt, row[0]) for row in rows)

    async def start_span(
        self, job: InvestigationJob, worker_id: str, kind: str, operation: str,
        version: str, parent_id: UUID | None = None,
    ) -> UUID:
        row = await (await self.connection.execute(
            "select public.create_investigation_span(%s,%s,%s,%s,%s,%s,%s)",
            (job.id, worker_id, job.attempt_count, kind, operation, version, parent_id),
        )).fetchone()
        if row is None or not isinstance(row[0], UUID):
            raise ValueError("span was not created")
        return row[0]

    async def finish_span(
        self, span_id: UUID, job: InvestigationJob, worker_id: str, status: str,
    ) -> None:
        await self.connection.execute(
            "select public.finish_investigation_span(%s,%s,%s,%s)",
            (span_id, worker_id, job.attempt_count, status),
        )

    async def finish_request(
        self, request_id: UUID, job: InvestigationJob, worker_id: str, status: str,
        issues: tuple[ValidationIssue, ...], model: str, prompt_hash: str, prompt_git_sha: str,
        parent_span_id: UUID, input_tokens: int | None, output_tokens: int | None, latency_ms: int,
    ) -> None:
        await self.connection.execute(
            "select public.finish_model_request(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (request_id, worker_id, job.attempt_count, status,
             Jsonb([issue.model_dump(mode="json") for issue in issues]), model, prompt_hash,
             prompt_git_sha, parent_span_id, input_tokens, output_tokens, latency_ms),
        )

    async def complete(self, job: InvestigationJob, worker_id: str) -> None:
        await self.connection.execute(
            "select public.complete_stage_job(%s,%s,%s)", (job.id, worker_id, job.attempt_count),
        )

    async def assert_lease(self, job: InvestigationJob, worker_id: str) -> None:
        await self.connection.execute(
            "select public.phase3_assert_lease(%s,%s,%s)", (job.id, worker_id, job.attempt_count),
        )

    async def events(self, investigation_id: UUID) -> tuple[InvestigationEvent, ...]:
        rows = await (await self.connection.execute(
            "select to_jsonb(e) from public.investigation_events e "
            "where investigation_id=%s order by sequence", (investigation_id,),
        )).fetchall()
        return tuple(_record(InvestigationEvent, row[0]) for row in rows)

    async def projection(self, investigation_id: UUID) -> InvestigationProjection:
        row = await (await self.connection.execute(
            """
            select jsonb_build_object(
                'investigation_id', i.id, 'case_id', i.case_id, 'status', i.status,
                'current_stage', i.current_stage, 'configuration_hash', i.configuration_hash,
                'event_head_hash', i.event_head_hash,
                'entities', coalesce((select jsonb_object_agg(e.id, to_jsonb(e) || jsonb_build_object(
                    'source_candidate_ids', array(select candidate_id from public.entity_candidate_links
                        where entity_id=e.id order by candidate_id),
                    'evidence_ids', array(select evidence_id from public.entity_evidence_links
                        where entity_id=e.id order by evidence_id)))
                    from public.investigation_entities e where e.investigation_id=i.id), '{}'),
                'timeline', coalesce((select jsonb_object_agg(t.id, to_jsonb(t) || jsonb_build_object(
                    'source_candidate_ids', array(select candidate_id from public.timeline_candidate_links
                        where timeline_event_id=t.id order by candidate_id),
                    'evidence_ids', array(select evidence_id from public.timeline_evidence_links
                        where timeline_event_id=t.id order by evidence_id)))
                    from public.timeline_events t where t.investigation_id=i.id), '{}'),
                'claims', coalesce((select jsonb_object_agg(c.id, to_jsonb(c) || jsonb_build_object(
                    'source_candidate_ids', array(select candidate_id from public.claim_candidate_links
                        where claim_id=c.id order by candidate_id),
                    'supporting_evidence_ids', array(select evidence_id from public.claim_evidence_links
                        where claim_id=c.id and polarity='SUPPORTING' order by evidence_id),
                    'contradicting_evidence_ids', array(select evidence_id from public.claim_evidence_links
                        where claim_id=c.id and polarity='CONTRADICTING' order by evidence_id)))
                    from public.claims c where c.investigation_id=i.id), '{}'),
                'hypotheses', coalesce((select jsonb_object_agg(h.id,to_jsonb(h))
                    from public.hypotheses h where h.investigation_id=i.id), '{}'),
                'hypothesis_claim_links', coalesce((select jsonb_object_agg(h.id,jsonb_build_object(
                    'supporting', array(select claim_id from public.hypothesis_claim_links
                        where hypothesis_id=h.id and polarity='SUPPORTING' order by claim_id),
                    'contradicting', array(select claim_id from public.hypothesis_claim_links
                        where hypothesis_id=h.id and polarity='CONTRADICTING' order by claim_id)))
                    from public.hypotheses h where h.investigation_id=i.id), '{}'),
                'questions', coalesce((select jsonb_object_agg(q.id,to_jsonb(q))
                    from public.unresolved_questions q where q.investigation_id=i.id), '{}'),
                'critiques', coalesce((select jsonb_object_agg(c.id,to_jsonb(c) || jsonb_build_object(
                    'proposed_tests', coalesce((select jsonb_agg(jsonb_build_object(
                        'type',t.type, 'expected_observation',t.expected_observation,
                        'strength',t.strength, 'execution_kind',t.execution_kind,
                        'parameters',t.parameters, 'evidence_ids',t.evidence_ids, 'claim_ids',t.claim_ids
                    ) order by t.id) from public.hypothesis_tests t where t.critique_id=c.id), '[]')))
                    from public.hypothesis_critiques c where c.investigation_id=i.id), '{}'),
                'tests', coalesce((select jsonb_object_agg(t.id,to_jsonb(t))
                    from public.hypothesis_tests t where t.investigation_id=i.id), '{}'),
                'revisions', coalesce((select jsonb_object_agg(r.id,to_jsonb(r) || jsonb_build_object(
                    'test_deltas', coalesce((select jsonb_agg(jsonb_build_object(
                        'test_id',d.test_id, 'delta',d.delta) order by d.ordinal)
                        from public.confidence_revision_test_deltas d where d.revision_id=r.id), '[]')))
                    from public.confidence_revisions r where r.investigation_id=i.id), '{}'),
                'retrievals', coalesce((select jsonb_object_agg(q.id,jsonb_build_object(
                    'query_id',q.id, 'hypothesis_id',q.hypothesis_id, 'model_run_id',q.model_run_id,
                    'query',jsonb_build_object(
                        'investigation_id',q.investigation_id, 'case_id',q.case_id,
                        'intent',q.intent, 'query_text',q.query_text, 'evidence_types',q.evidence_types,
                        'document_types',q.document_types, 'entity_ids',q.entity_ids,
                        'start_at',q.start_at, 'end_at',q.end_at, 'limit',q.result_limit,
                        'config_version',q.config_version),
                    'results',coalesce((select jsonb_agg(jsonb_build_object(
                        'evidence_id',r.evidence_id, 'rank',r.rank, 'total_score',r.total_score::text,
                        'fts_score',r.fts_score::text, 'entity_score',r.entity_score::text,
                        'time_score',r.time_score::text, 'type_score',r.type_score::text,
                        'matched_filters',r.matched_filters) order by r.rank)
                        from public.retrieval_results r where r.query_id=q.id), '[]')))
                    from public.retrieval_queries q where q.investigation_id=i.id), '{}')
            ) from public.investigations i where i.id=%s
            """, (investigation_id,),
        )).fetchone()
        if row is None:
            raise LookupError("investigation not accessible")
        return _record(InvestigationProjection, row[0])

    async def evidence(self, investigation_id: UUID) -> tuple[EvidenceItem, ...]:
        rows = await (await self.connection.execute(
            "select e.item from public.investigation_evidence p "
            "join public.evidence_items e on e.id=p.evidence_id and e.case_id=p.case_id "
            "and e.model_run_id is not distinct from p.model_run_id "
            "join public.source_documents s on s.id=e.source_document_id and s.case_id=e.case_id "
            "left join public.semantic_unit_completions c on c.structural_unit_id=e.structural_unit_id "
            "and c.model_run_id=e.model_run_id where p.investigation_id=%s "
            "and s.visibility='INVESTIGATION_EVIDENCE' and e.review_status in ('NOT_REQUIRED','ACCEPTED') "
            "and (c.model_run_id is not null or (e.model_run_id is null "
            "and e.item->>'extraction_method' in ('DETERMINISTIC','HUMAN'))) order by e.id",
            (investigation_id,),
        )).fetchall()
        return tuple(EvidenceItem.model_validate(row[0], strict=False) for row in rows)

    async def fail(self, job: InvestigationJob, worker_id: str, failure: FailureCode) -> None:
        await self.connection.execute(
            "select public.fail_stage_job(%s,%s,%s,%s)",
            (job.id, worker_id, job.attempt_count, failure.value),
        )
