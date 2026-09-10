from typing import Annotated
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import Field, TypeAdapter

from casezero_investigation.canonical import canonical_digest
from casezero_investigation.confidence import assign_hypothesis_statuses
from casezero_investigation.events import (
    _TARGET_TYPES,
    ClaimCreatedPayload,
    ConfidenceRevisedPayload,
    CritiqueCreatedPayload,
    EntityCreatedPayload,
    HypothesisCreatedPayload,
    HypothesisStatusChangedPayload,
    InvestigationCompletedPayload,
    RetrievalCompletedPayload,
    TestCreatedPayload,
    TestResolvedPayload,
    TimelineCreatedPayload,
    _event_data,
)
from casezero_investigation.hypotheses import HypothesisTestStatus
from casezero_investigation.jobs import InvestigationJob, JobStatus
from casezero_investigation.models import InvestigationStage as Stage
from casezero_investigation.models import InvestigationStatus
from casezero_investigation.replay import InvestigationProjection, ReplayError, replay
from casezero_investigation.repository import InvestigationRepository, _record

StageMutation = Annotated[
    EntityCreatedPayload | TimelineCreatedPayload | ClaimCreatedPayload | HypothesisCreatedPayload
    | CritiqueCreatedPayload | TestCreatedPayload | TestResolvedPayload | ConfidenceRevisedPayload
    | HypothesisStatusChangedPayload | RetrievalCompletedPayload | InvestigationCompletedPayload,
    Field(discriminator="kind"),
]
_MUTATIONS = TypeAdapter(tuple[StageMutation, ...])
_ALLOWED: dict[Stage, tuple[type[StageMutation], ...]] = {
    Stage.PROMOTE_TIMELINE: (TimelineCreatedPayload,),
    Stage.RESOLVE_ENTITIES: (EntityCreatedPayload,),
    Stage.PROMOTE_CLAIMS: (ClaimCreatedPayload,),
    Stage.GENERATE_HYPOTHESES: (HypothesisCreatedPayload,),
    Stage.SEARCH_SUPPORT: (RetrievalCompletedPayload,),
    Stage.SEARCH_CONTRADICTIONS: (RetrievalCompletedPayload,),
    Stage.DESIGN_FALSIFICATION_TESTS: (CritiqueCreatedPayload, TestCreatedPayload),
    Stage.EXECUTE_FALSIFICATION_TESTS: (TestResolvedPayload,),
    Stage.REVISE_CONFIDENCE: (ConfidenceRevisedPayload, HypothesisStatusChangedPayload),
    Stage.VERIFY_REPLAY: (),
    Stage.COMPLETE: (InvestigationCompletedPayload,),
}


async def _fence(repository: InvestigationRepository, job: InvestigationJob, worker_id: str) -> None:
    row = await (await repository.connection.execute(
        "select to_jsonb(j) from public.phase3_assert_lease(%s,%s,%s) j",
        (job.id, worker_id, job.attempt_count),
    )).fetchone()
    if row is None:
        raise ValueError("job lease is not owned")
    current = _record(InvestigationJob, row[0])
    fields = {"id", "investigation_id", "stage", "work_key", "input_state_hash", "status",
              "worker_id", "attempt_count", "claimed_at", "lease_expires_at"}
    if job.status is not JobStatus.RUNNING or current.model_dump(include=fields) != job.model_dump(include=fields):
        raise ValueError("claimed job context mismatch")
    investigation = await repository.get(job.investigation_id)
    if investigation.current_stage is not job.stage or investigation.status is not InvestigationStatus.RUNNING:
        raise ValueError("investigation stage is not running")


async def _receipt(
    repository: InvestigationRepository, job: InvestigationJob, run_id: UUID, *, failed: bool = False,
) -> None:
    row = await (await repository.connection.execute(
        "select 1 from public.model_runs m join public.model_request_attempts r on r.id=m.id "
        "join public.investigations i on i.id=m.investigation_id and i.case_id=m.case_id "
        "where m.id=%s and m.job_id=%s and m.investigation_id=%s and m.stage=%s "
        "and r.job_id=m.job_id and r.investigation_id=m.investigation_id and r.case_id=m.case_id "
        "and m.status=%s and r.status=any(%s)",
        (run_id, job.id, job.investigation_id, job.stage.value, "FAILED" if failed else "SUCCEEDED",
         ["SCHEMA_FAILED", "PROVIDER_FAILED"] if failed else ["SUCCEEDED"]),
    )).fetchone()
    if row is None:
        raise ValueError("canonical model attribution requires a matching same-job Phase3 receipt")


async def _verify(repository: InvestigationRepository, investigation_id: UUID) -> InvestigationProjection:
    stored = await repository.projection(investigation_id)
    reconstructed = replay(await repository.events(investigation_id))
    if stored.canonical_state() != reconstructed.canonical_state():
        raise ReplayError("relational projection does not match replay")
    if (stored.status, stored.current_stage, stored.configuration_hash, stored.event_head_hash) != (
        reconstructed.status, reconstructed.current_stage,
        reconstructed.configuration_hash, reconstructed.event_head_hash,
    ):
        raise ReplayError("investigation lifecycle does not match replay")
    return reconstructed


async def persist_stage_result(
    connection: AsyncConnection[tuple[object, ...]], job: InvestigationJob, worker_id: str,
    span_id: UUID, mutations: tuple[StageMutation, ...],
) -> None:
    repository = InvestigationRepository(connection)
    async with connection.transaction():
        await _fence(repository, job, worker_id)
        await connection.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s,0))", (str(job.investigation_id),),
        )
        await _fence(repository, job, worker_id)
        span = await (await connection.execute(
            "select 1 from public.investigation_spans where id=%s and job_id=%s "
            "and investigation_id=%s and kind='AGENT' and status='RUNNING' and started_at >= %s",
            (span_id, job.id, job.investigation_id, job.claimed_at),
        )).fetchone()
        if span is None:
            raise ValueError("current job requires a running AGENT span")
        mutations = _MUTATIONS.validate_python(_event_data(mutations))
        if any(type(mutation) not in _ALLOWED[job.stage] for mutation in mutations):
            raise ValueError("unsupported mutation for current stage")
        before = await _verify(repository, job.investigation_id)
        eligible = {item.id for item in await repository.evidence(job.investigation_id)}
        for mutation in mutations:
            if isinstance(mutation, (HypothesisStatusChangedPayload, InvestigationCompletedPayload)):
                continue
            if isinstance(mutation, RetrievalCompletedPayload):
                if (mutation.query.investigation_id, mutation.query.case_id) != (before.investigation_id, before.case_id):
                    raise ValueError("retrieval context mismatch")
                if str(mutation.hypothesis_id) != job.work_key:
                    raise ValueError("retrieval hypothesis does not match job work key")
                intent = "SUPPORT" if job.stage is Stage.SEARCH_SUPPORT else "CONTRADICT"
                if mutation.query.intent.value != intent:
                    raise ValueError("retrieval intent does not match stage")
                if mutation.model_run_id is not None:
                    await _receipt(repository, job, mutation.model_run_id)
                continue
            record = mutation.record
            if (record.investigation_id, record.case_id) != (before.investigation_id, before.case_id):
                raise ValueError("canonical record context mismatch")
            if not isinstance(mutation, ConfidenceRevisedPayload) and mutation.record.model_run_id is not None:
                await _receipt(
                    repository, job, mutation.record.model_run_id,
                    failed=isinstance(mutation, TestResolvedPayload)
                    and mutation.record.status is HypothesisTestStatus.FAILED,
                )
            if (
                isinstance(mutation, (CritiqueCreatedPayload, TestCreatedPayload))
                and str(mutation.record.hypothesis_id) != job.work_key
            ):
                raise ValueError("critique/test hypothesis does not match job work key")
            if isinstance(mutation, TestCreatedPayload) and mutation.record.job_id != job.id:
                raise ValueError("created test job context mismatch")
            if isinstance(mutation, TestResolvedPayload) and str(mutation.record.id) != job.work_key:
                raise ValueError("resolved test does not match job work key")
            if (
                isinstance(mutation, (TestCreatedPayload, TestResolvedPayload))
                and not {*mutation.record.evidence_ids, *mutation.record.result_evidence_ids} <= eligible
            ):
                raise ValueError("test evidence reference outside pinned investigation")
            if isinstance(mutation, CritiqueCreatedPayload) and any(
                not set(draft.evidence_ids) <= eligible for draft in mutation.record.proposed_tests
            ):
                raise ValueError("proposed test evidence reference outside pinned investigation")
        if job.stage is Stage.COMPLETE and len(mutations) != 1:
            raise ValueError("COMPLETE requires one projection-hash completion")
        if job.stage is Stage.GENERATE_HYPOTHESES and not 3 <= len(mutations) <= 5:
            raise ValueError("hypotheses generation requires 3 to 5 competing hypotheses")
        if (
            job.stage in {Stage.SEARCH_SUPPORT, Stage.SEARCH_CONTRADICTIONS, Stage.DESIGN_FALSIFICATION_TESTS}
            and job.work_key != "case" and not mutations
        ):
            raise ValueError("hypothesis work requires a validated result")
        candidate_kind = {
            Stage.PROMOTE_TIMELINE: "timeline", Stage.RESOLVE_ENTITIES: "entity", Stage.PROMOTE_CLAIMS: "claim",
        }.get(job.stage)
        if candidate_kind is not None:
            offset = 0 if job.work_key == "case" else int(job.work_key) * 5
            candidates = await (await connection.execute(
                "select candidate_id from public.investigation_candidates "
                "where investigation_id=%s and kind=%s order by candidate_id offset %s limit 5",
                (job.investigation_id, candidate_kind, offset),
            )).fetchall()
            referenced = tuple(
                identifier for mutation in mutations
                if isinstance(mutation, (TimelineCreatedPayload, EntityCreatedPayload, ClaimCreatedPayload))
                for identifier in mutation.record.source_candidate_ids
            )
            if len(referenced) != len(set(referenced)) or set(referenced) != {row[0] for row in candidates}:
                raise ValueError("promotion must cover each candidate in its job batch exactly once")
        if job.stage is Stage.EXECUTE_FALSIFICATION_TESTS and job.work_key != "case" and len(mutations) != 1:
            raise ValueError("test execution requires one resolved test")
        for mutation in mutations:
            await _write(connection, job, mutation)
            target_id = job.investigation_id
            model_run_id = None
            if isinstance(mutation, RetrievalCompletedPayload):
                target_id = mutation.query_id
                model_run_id = mutation.model_run_id
            elif isinstance(mutation, HypothesisStatusChangedPayload):
                target_id = mutation.hypothesis_id
            elif not isinstance(mutation, InvestigationCompletedPayload):
                target_id = mutation.record.id
                if not isinstance(mutation, ConfidenceRevisedPayload):
                    model_run_id = mutation.record.model_run_id
            await connection.execute(
                "select public.append_investigation_event(%s,%s,%s,%s,%s,%s)",
                (job.investigation_id, mutation.kind, _TARGET_TYPES[mutation.kind], target_id,
                 Jsonb(mutation.model_dump(mode="json")), model_run_id),
            )
        after = replay(await repository.events(job.investigation_id))
        stored = await repository.projection(job.investigation_id)
        if stored.canonical_state() != after.canonical_state():
            raise ReplayError("persisted relational projection does not match stage result")
        if job.stage is Stage.REVISE_CONFIDENCE:
            if {r.hypothesis_id for r in after.revisions.values()} != set(after.hypotheses):
                raise ValueError("confidence stage must revise every hypothesis")
            if tuple(after.hypotheses.values()) != assign_hypothesis_statuses(tuple(after.hypotheses.values())):
                raise ValueError("confidence stage must apply deterministic hypothesis statuses")
        await _fence(repository, job, worker_id)
        await repository.finish_span(span_id, job, worker_id, "SUCCEEDED")
        await _fence(repository, job, worker_id)
        await repository.complete(job, worker_id)
        await _verify(repository, job.investigation_id)


async def _write(
    connection: AsyncConnection[tuple[object, ...]], job: InvestigationJob, mutation: StageMutation,
) -> None:
    if isinstance(mutation, EntityCreatedPayload):
        entity = mutation.record
        await connection.execute(
            "insert into public.investigation_entities select * from "
            "jsonb_populate_record(null::public.investigation_entities,%s)",
            (Jsonb(entity.model_dump(mode="json")),),
        )
        for candidate_id in entity.source_candidate_ids:
            await connection.execute(
                "insert into public.entity_candidate_links values(%s,%s,%s,%s)",
                (entity.investigation_id, entity.case_id, entity.id, candidate_id),
            )
        for evidence_id in entity.evidence_ids:
            await connection.execute(
                "insert into public.entity_evidence_links values(%s,%s,%s,%s)",
                (entity.investigation_id, entity.case_id, entity.id, evidence_id),
            )
    elif isinstance(mutation, TimelineCreatedPayload):
        timeline = mutation.record
        await connection.execute(
            "insert into public.timeline_events select * from jsonb_populate_record(null::public.timeline_events,%s)",
            (Jsonb(timeline.model_dump(mode="json")),),
        )
        for candidate_id in timeline.source_candidate_ids:
            await connection.execute(
                "insert into public.timeline_candidate_links values(%s,%s,%s,%s)",
                (timeline.investigation_id, timeline.case_id, timeline.id, candidate_id),
            )
        for evidence_id in timeline.evidence_ids:
            await connection.execute(
                "insert into public.timeline_evidence_links values(%s,%s,%s,%s)",
                (timeline.investigation_id, timeline.case_id, timeline.id, evidence_id),
            )
    elif isinstance(mutation, ClaimCreatedPayload):
        claim = mutation.record
        await connection.execute(
            "insert into public.claims select * from jsonb_populate_record(null::public.claims,%s)",
            (Jsonb(claim.model_dump(mode="json")),),
        )
        for candidate_id in claim.source_candidate_ids:
            await connection.execute(
                "insert into public.claim_candidate_links values(%s,%s,%s,%s)",
                (claim.investigation_id, claim.case_id, claim.id, candidate_id),
            )
        for polarity, evidence_ids in (("SUPPORTING", claim.supporting_evidence_ids),
                                       ("CONTRADICTING", claim.contradicting_evidence_ids)):
            for evidence_id in evidence_ids:
                await connection.execute(
                    "insert into public.claim_evidence_links values(%s,%s,%s,%s,%s)",
                    (claim.investigation_id, claim.case_id, claim.id, evidence_id, polarity),
                )
    elif isinstance(mutation, HypothesisCreatedPayload):
        hypothesis = mutation.record
        await connection.execute(
            "insert into public.hypotheses select * from jsonb_populate_record(null::public.hypotheses,%s)",
            (Jsonb(hypothesis.model_dump(mode="json")),),
        )
        for polarity, claim_ids in (("SUPPORTING", mutation.supporting_claim_ids),
                                    ("CONTRADICTING", mutation.contradicting_claim_ids)):
            for claim_id in claim_ids:
                await connection.execute(
                    "insert into public.hypothesis_claim_links values(%s,%s,%s,%s,%s)",
                    (hypothesis.investigation_id, hypothesis.case_id, hypothesis.id, claim_id, polarity),
                )
        for question in mutation.questions:
            await connection.execute(
                "insert into public.unresolved_questions select * from "
                "jsonb_populate_record(null::public.unresolved_questions,%s)",
                (Jsonb(question.model_dump(mode="json")),),
            )
    elif isinstance(mutation, CritiqueCreatedPayload):
        await connection.execute(
            "insert into public.hypothesis_critiques select * from "
            "jsonb_populate_record(null::public.hypothesis_critiques,%s)",
            (Jsonb(mutation.record.model_dump(mode="json")),),
        )
    elif isinstance(mutation, TestCreatedPayload):
        await connection.execute(
            "insert into public.hypothesis_tests select * from jsonb_populate_record(null::public.hypothesis_tests,%s)",
            (Jsonb(mutation.record.model_dump(mode="json")),),
        )
    elif isinstance(mutation, TestResolvedPayload):
        test = mutation.record
        await connection.execute(
            "update public.hypothesis_tests set status=%s,outcome=%s,model_run_id=%s,"
            "completed_at=%s,result_evidence_ids=%s where id=%s and investigation_id=%s and status='PENDING'",
            (test.status.value, test.outcome.value if test.outcome else None, test.model_run_id,
             test.completed_at, list(test.result_evidence_ids), test.id, job.investigation_id),
        )
    elif isinstance(mutation, ConfidenceRevisedPayload):
        revision = mutation.record
        await connection.execute(
            "insert into public.confidence_revisions select * from jsonb_populate_record(null::public.confidence_revisions,%s)",
            (Jsonb(revision.model_dump(mode="json")),),
        )
        for ordinal, delta in enumerate(revision.test_deltas):
            await connection.execute(
                "insert into public.confidence_revision_test_deltas values(%s,%s,%s,%s,%s,%s)",
                (revision.investigation_id, revision.case_id, revision.id, delta.test_id, ordinal, delta.delta),
            )
        await connection.execute(
            "update public.hypotheses set current_confidence=%s where id=%s and investigation_id=%s",
            (revision.after, revision.hypothesis_id, job.investigation_id),
        )
    elif isinstance(mutation, HypothesisStatusChangedPayload):
        await connection.execute(
            "update public.hypotheses set status=%s where id=%s and investigation_id=%s",
            (mutation.after.value, mutation.hypothesis_id, job.investigation_id),
        )
    elif isinstance(mutation, RetrievalCompletedPayload):
        query = mutation.query
        await connection.execute(
            "insert into public.retrieval_queries(id,investigation_id,case_id,hypothesis_id,job_id,"
            "intent,query_text,evidence_types,document_types,entity_ids,start_at,end_at,result_limit,"
            "config_version,query_hash,model_run_id,created_at) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
            (mutation.query_id, query.investigation_id, query.case_id, mutation.hypothesis_id, job.id,
             query.intent.value, query.query_text, list(query.evidence_types), list(query.document_types),
             list(query.entity_ids), query.start_at, query.end_at, query.limit, query.config_version,
             canonical_digest(query.canonical_payload()), mutation.model_run_id),
        )
        for question in mutation.questions:
            await connection.execute(
                "insert into public.unresolved_questions select * from "
                "jsonb_populate_record(null::public.unresolved_questions,%s)",
                (Jsonb(question.model_dump(mode="json")),),
            )
        for result in mutation.results:
            await connection.execute(
                "insert into public.retrieval_results values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (query.investigation_id, query.case_id, mutation.query_id, result.evidence_id, result.rank,
                 result.total_score, result.fts_score, result.entity_score, result.time_score,
                 result.type_score, list(result.matched_filters)),
            )
