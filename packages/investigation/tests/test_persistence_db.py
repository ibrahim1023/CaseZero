import os
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from casezero_investigation.canonical import canonical_digest
from casezero_investigation.confidence import assign_hypothesis_statuses, revise_confidence
from casezero_investigation.events import (
    ClaimCreatedPayload,
    ConfidenceRevisedPayload,
    CritiqueCreatedPayload,
    EntityCreatedPayload,
    HypothesisCreatedPayload,
    HypothesisStatusChangedPayload,
    InvestigationCompletedPayload,
    RetrievalCompletedPayload,
    TimelineCreatedPayload,
)
from casezero_investigation.events import TestCreatedPayload as CreatedTest
from casezero_investigation.events import TestResolvedPayload as ResolvedTest
from casezero_investigation.hypotheses import (
    ExecutionKind,
    HypothesisCritique,
    HypothesisTest,
    HypothesisTestDraft,
    HypothesisTestStatus,
)
from casezero_investigation.hypotheses import TestOutcome as Outcome
from casezero_investigation.hypotheses import TestStrength as Strength
from casezero_investigation.hypotheses import TestType as Kind
from casezero_investigation.models import (
    Claim,
    ClaimStatus,
    Hypothesis,
    HypothesisStatus,
    InvestigationEntity,
    InvestigationStatus,
    TimelineEvent,
    TimePrecision,
    UnresolvedQuestion,
)
from casezero_investigation.models import InvestigationStage as Stage
from casezero_investigation.replay import replay
from casezero_investigation.repository import InvestigationRepository
from casezero_retrieval.models import EvidenceSearchQuery, EvidenceSearchResult, RetrievalIntent
from psycopg import AsyncConnection, Error

from .db_support import NOW, seed_case
from .test_repository_db import CONFIG

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1"),
]


@dataclass
class State:
    connection: AsyncConnection
    repository: InvestigationRepository
    investigation_id: UUID
    case_id: UUID
    evidence_ids: tuple[UUID, ...]
    candidates: dict[str, tuple[UUID, ...]]
    legacy_run: UUID

    async def claimed(self):
        job = await self.repository.claim(self.investigation_id, "worker")
        assert job is not None
        span = await self.repository.start_span(job, "worker", "AGENT", job.stage.value, "test-v1")
        return job, span

    async def receipt(self, job, span, status="SUCCEEDED"):
        request = await self.repository.reserve_request(job.id, "worker", job.attempt_count)
        await self.repository.finish_request(
            request.id, job, "worker", status, (), "fixture", "a" * 64, "fc3bda6", span, 1, 1, 1,
        )
        return request.id

    async def persist(self, job, span, mutations, worker="worker"):
        from casezero_investigation.persistence import persist_stage_result

        await persist_stage_result(self.connection, job, worker, span, mutations)

    def timeline(self, run_id):
        return TimelineCreatedPayload(record=TimelineEvent(
            investigation_id=self.investigation_id, case_id=self.case_id,
            occurred_at=NOW, time_precision=TimePrecision.EXACT, description="Synthetic power loss.",
            confidence=Decimal("0.9"), source_candidate_ids=self.candidates["timeline"],
            evidence_ids=(self.evidence_ids[0],), model_run_id=run_id, created_at=NOW,
        ))

    async def checkpoint(self):
        return await (await self.connection.execute(
            "select i.status,i.current_stage,i.event_sequence,"
            "(select count(*) from timeline_events where investigation_id=i.id),"
            "(select count(*) from timeline_candidate_links where investigation_id=i.id),"
            "(select count(*) from timeline_evidence_links where investigation_id=i.id),"
            "(select jsonb_agg(to_jsonb(j) order by id) from investigation_jobs j where investigation_id=i.id),"
            "(select jsonb_agg(to_jsonb(s) order by id) from investigation_spans s where investigation_id=i.id) "
            "from investigations i where i.id=%s", (self.investigation_id,),
        )).fetchone()


@pytest.fixture
async def state():
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"]) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, evidence, _ = await seed_case(connection, complete=True)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        investigation = await repository.create(case_id, CONFIG)
        rows = await (await connection.execute(
            "select kind,array_agg(candidate_id order by candidate_id) from investigation_candidates "
            "where investigation_id=%s group by kind", (investigation.id,),
        )).fetchall()
        yield State(
            connection, repository, investigation.id, case_id,
            tuple(item.id for item in await repository.evidence(investigation.id)),
            {kind: tuple(ids) for kind, ids in rows}, evidence.model_run_id,
        )


@pytest.mark.parametrize("with_receipt", (False, True))
async def test_empty_promotion_cannot_drop_candidates_even_with_a_receipt(state, with_receipt):
    job, span = await state.claimed()
    if with_receipt:
        await state.receipt(job, span)
    before = await state.checkpoint()
    with pytest.raises(ValueError, match="candidate"):
        await state.persist(job, span, ())
    assert await state.checkpoint() == before


@pytest.mark.parametrize("duplicate", (False, True))
async def test_promotion_requires_exact_once_candidate_coverage(state, duplicate):
    job, span = await state.claimed()
    mutation = state.timeline(await state.receipt(job, span))
    partial = TimelineCreatedPayload(record=mutation.record.model_copy(update={
        "source_candidate_ids": state.candidates["timeline"][:1],
    }))
    mutations = (mutation, partial.model_copy(update={"record": partial.record.model_copy(update={"id": uuid4()})})) if duplicate else (partial,)
    before = await state.checkpoint()
    with pytest.raises(ValueError, match="candidate"):
        await state.persist(job, span, mutations)
    assert await state.checkpoint() == before


async def test_empty_deterministic_promotion_requires_no_fake_model_receipt(state):
    await state.connection.execute("reset role")
    case_id, _, _ = await seed_case(state.connection)
    await state.connection.execute("set local role casezero_blind")
    inv = await state.repository.create(case_id, CONFIG)
    job = await state.repository.claim(inv.id, "worker")
    span = await state.repository.start_span(job, "worker", "AGENT", job.stage.value, "v1")
    await state.persist(job, span, ())
    assert await state.repository.requests(job.id) == ()
    assert (await state.repository.get(inv.id)).current_stage is Stage.RESOLVE_ENTITIES


@pytest.mark.parametrize("failure", ("unsupported", "record_context", "candidate", "duplicate", "forged_payload"))
async def test_rejects_invalid_mutation_structure_context_lineage_and_rewrites(state, failure):
    job, span = await state.claimed()
    mutation = state.timeline(await state.receipt(job, span))
    mutations = (mutation,)
    if failure == "unsupported":
        mutations = (InvestigationCompletedPayload(projection_hash="a" * 64),)
    elif failure == "record_context":
        mutations = (TimelineCreatedPayload(record=mutation.record.model_copy(update={"case_id": uuid4()})),)
    elif failure == "candidate":
        mutations = (TimelineCreatedPayload(record=mutation.record.model_copy(update={"source_candidate_ids": (uuid4(),)})),)
    elif failure == "duplicate":
        mutations = (mutation, mutation)
    else:
        mutations = (mutation.model_copy(update={"schema_version": "2"}),)
    before = await state.checkpoint()
    with pytest.raises((ValueError, Error)):
        await state.persist(job, span, mutations)
    assert await state.checkpoint() == before


async def test_claim_records_initial_running_transition_once(state):
    job, _ = await state.claimed()
    events = await state.repository.events(state.investigation_id)
    assert len(events) == 2
    assert replay(events).status is InvestigationStatus.RUNNING
    await expire(state, job)
    await state.claimed()
    assert await state.repository.events(state.investigation_id) == events


async def test_invalid_final_link_rolls_back_rows_events_completion_but_not_receipt(state):
    job, span = await state.claimed()
    run = await state.receipt(job, span)
    good = state.timeline(run)
    invalid = TimelineCreatedPayload(record=good.record.model_copy(update={
        "evidence_ids": (state.evidence_ids[0], uuid4()),
    }))
    before = await state.checkpoint()
    with pytest.raises(Error):
        await state.persist(job, span, (invalid,))
    assert await state.checkpoint() == before
    assert (await state.repository.requests(job.id))[0].status.value == "SUCCEEDED"


async def test_success_persists_rows_links_events_span_and_declared_next_job(state):
    job, span = await state.claimed()
    mutation = state.timeline(await state.receipt(job, span))
    await state.persist(job, span, (mutation,))
    projected = await state.repository.projection(state.investigation_id)
    assert projected.canonical_state() == replay(
        await state.repository.events(state.investigation_id),
    ).canonical_state()
    assert projected.timeline[mutation.record.id] == mutation.record
    checkpoint = await state.checkpoint()
    assert checkpoint[:6] == ("RUNNING", "RESOLVE_ENTITIES", 4, 1, 2, 1)
    assert {(j["stage"], j["status"]) for j in checkpoint[6]} == {
        ("PROMOTE_TIMELINE", "SUCCEEDED"), ("RESOLVE_ENTITIES", "PENDING"),
    }
    assert checkpoint[7][0]["status"] == "SUCCEEDED"


async def expire(state, job):
    await state.connection.execute("reset role")
    await state.connection.execute(
        "update investigation_jobs set claimed_at=clock_timestamp()-interval '1 hour',"
        "lease_expires_at=clock_timestamp()-interval '1 second' where id=%s", (job.id,),
    )
    await state.connection.execute("set local role casezero_blind")


@pytest.mark.parametrize("failure", ("worker", "expired", "reclaimed", "context", "stage", "span", "tool"))
async def test_fences_reject_wrong_lease_job_context_stage_and_span(state, failure):
    job, span = await state.claimed()
    mutation = state.timeline(await state.receipt(job, span))
    worker = "worker"
    if failure == "worker":
        worker = "intruder"
    elif failure in {"expired", "reclaimed"}:
        await expire(state, job)
        if failure == "reclaimed":
            await state.claimed()
    elif failure == "context":
        job = job.model_copy(update={"investigation_id": uuid4()})
    elif failure == "stage":
        job = job.model_copy(update={"stage": Stage.RESOLVE_ENTITIES})
    elif failure == "span":
        span = uuid4()
    else:
        span = await state.repository.start_span(job, worker, "TOOL", "fixture", "v1")
    before = await state.checkpoint()
    with pytest.raises((ValueError, Error)):
        await state.persist(job, span, (mutation,), worker)
    assert await state.checkpoint() == before


@pytest.mark.parametrize("failure", ("legacy", "failed", "other_investigation", "other_case", "other_job"))
async def test_model_attribution_requires_same_job_successful_phase3_receipt(state, failure):
    job, span = await state.claimed()
    if failure == "legacy":
        run = state.legacy_run
    elif failure == "failed":
        run = await state.receipt(job, span, "PROVIDER_FAILED")
    elif failure == "other_job":
        run = await state.receipt(job, span)
        await state.persist(job, span, (state.timeline(run),))
        job, span = await state.claimed()
    else:
        case_id = state.case_id
        if failure == "other_case":
            await state.connection.execute("reset role")
            case_id, _, _ = await seed_case(state.connection)
            await state.connection.execute("set local role casezero_blind")
        other = await state.repository.create(case_id, {**CONFIG, "version": "other"})
        other_job = await state.repository.claim(other.id, "worker")
        other_span = await state.repository.start_span(other_job, "worker", "AGENT", "fixture", "v1")
        run = await state.receipt(other_job, other_span)
    mutation = state.timeline(run)
    if failure == "other_job":
        mutation = EntityCreatedPayload(record=InvestigationEntity(
            investigation_id=state.investigation_id, case_id=state.case_id,
            type="component", canonical_name="engine", evidence_ids=state.evidence_ids,
            source_candidate_ids=state.candidates["entity"], model_run_id=run, created_at=NOW,
        ))
    before = await state.checkpoint()
    with pytest.raises(ValueError, match="receipt"):
        await state.persist(job, span, (mutation,))
    assert await state.checkpoint() == before


@pytest.mark.parametrize("failure", ("span_completion", "lease_at_completion"))
async def test_completion_failures_roll_back_all_stage_writes(state, monkeypatch, failure):
    job, span = await state.claimed()
    mutation = state.timeline(await state.receipt(job, span))
    original = InvestigationRepository.finish_span

    async def fail(repository, *args):
        await original(repository, *args)
        if failure == "span_completion":
            raise ValueError("required span failed")
        await expire(state, job)

    monkeypatch.setattr(InvestigationRepository, "finish_span", fail)
    before = await state.checkpoint()
    with pytest.raises((ValueError, Error)):
        await state.persist(job, span, (mutation,))
    assert await state.checkpoint() == before


async def advance_full_path(state, stop=Stage.COMPLETE, *, semantic=False):
    while (await state.repository.get(state.investigation_id)).current_stage is not stop:
        job, span = await state.claimed()
        p = await state.repository.projection(state.investigation_id)
        context = {"investigation_id": state.investigation_id, "case_id": state.case_id, "created_at": NOW}
        mutations = ()
        if job.stage is Stage.PROMOTE_TIMELINE:
            mutations = (state.timeline(await state.receipt(job, span)),)
        elif job.stage is Stage.RESOLVE_ENTITIES:
            mutations = (EntityCreatedPayload(record=InvestigationEntity(
                **context, type="component", canonical_name="engine", aliases=("powerplant",),
                source_candidate_ids=state.candidates["entity"], evidence_ids=state.evidence_ids,
                model_run_id=await state.receipt(job, span),
            )),)
        elif job.stage is Stage.PROMOTE_CLAIMS:
            mutations = (ClaimCreatedPayload(record=Claim(
                **context, text="Power decreased.", status=ClaimStatus.OBSERVED, confidence=Decimal("0.8"),
                source_candidate_ids=state.candidates["claim"], supporting_evidence_ids=state.evidence_ids[:1],
                contradicting_evidence_ids=state.evidence_ids[1:], model_run_id=await state.receipt(job, span),
            )),)
        elif job.stage is Stage.GENERATE_HYPOTHESES:
            run = await state.receipt(job, span)
            hypotheses = tuple(Hypothesis(
                **context, title=title, description=description,
                initial_confidence=confidence, current_confidence=confidence,
                status=HypothesisStatus.ACTIVE, distinguishing_prediction="Power should recover.",
                weakening_evidence="Sustained power loss.", model_run_id=run,
            ) for title, description, confidence in (
                ("Fuel supply", "Synthetic fuel interruption mechanism.", Decimal("0.6")),
                ("Measurement fault", "Synthetic instrument reading anomaly.", Decimal("0.5")),
                ("Environmental load", "Synthetic ambient conditions limit performance.", Decimal("0.5")),
            ))
            mutations = tuple(HypothesisCreatedPayload(
                record=hypothesis, supporting_claim_ids=tuple(p.claims), contradicting_claim_ids=tuple(p.claims),
                questions=(UnresolvedQuestion(**context, hypothesis_id=hypothesis.id, text="Was power restored?"),),
            ) for hypothesis in hypotheses)
        elif job.stage in {Stage.SEARCH_SUPPORT, Stage.SEARCH_CONTRADICTIONS}:
            mutations = (RetrievalCompletedPayload(
                query_id=uuid4(), hypothesis_id=UUID(job.work_key),
                query=EvidenceSearchQuery(
                    investigation_id=state.investigation_id, case_id=state.case_id,
                    intent=RetrievalIntent.SUPPORT if job.stage is Stage.SEARCH_SUPPORT else RetrievalIntent.CONTRADICT,
                    query_text="power", entity_ids=tuple(p.entities), limit=2,
                ),
                results=tuple(EvidenceSearchResult(
                    evidence_id=eid, rank=rank, fts_score=Decimal("0.5"), entity_score=Decimal("0.33333333333333333333"),
                    time_score=Decimal(0), type_score=Decimal(0), total_score=Decimal("0.475"),
                    matched_filters=("entity",),
                ) for rank, eid in enumerate(reversed(state.evidence_ids), start=1)),
            ),)
        elif job.stage is Stage.DESIGN_FALSIFICATION_TESTS:
            drafts = tuple(HypothesisTestDraft(
                type=Kind.SEMANTIC_COMPARISON if semantic else Kind.EVIDENCE_PRESENCE,
                expected_observation=text, strength=strength,
                execution_kind=ExecutionKind.AI if semantic else ExecutionKind.DETERMINISTIC,
                parameters={"schema_version": "semantic-comparison-v1" if semantic else "evidence-presence-v1"},
                evidence_ids=state.evidence_ids[:1], claim_ids=tuple(p.claims),
            ) for text, strength in (("Power recovers.", Strength.LOW), ("Power changes.", Strength.MEDIUM)))
            critique = CritiqueCreatedPayload(record=HypothesisCritique(
                **context, hypothesis_id=UUID(job.work_key), strongest_contradiction_id=state.evidence_ids[1],
                missing_evidence=("Recorder",), alternative_explanation="Sensor issue", critique_confidence=Decimal("0.7"),
                proposed_tests=drafts, model_run_id=await state.receipt(job, span),
            ))
            mutations = (critique, *(CreatedTest(record=HypothesisTest(
                **context, **draft.model_dump(), hypothesis_id=UUID(job.work_key), critique_id=critique.record.id,
                job_id=job.id, status=HypothesisTestStatus.PENDING,
            )) for draft in critique.record.proposed_tests))
        elif job.stage is Stage.EXECUTE_FALSIFICATION_TESTS:
            test = p.tests[UUID(job.work_key)]
            mutations = (ResolvedTest(record=test.model_copy(update={
                "status": HypothesisTestStatus.SUCCEEDED, "outcome": Outcome.SURVIVED,
                "completed_at": NOW, "result_evidence_ids": state.evidence_ids[1:],
            })),)
        elif job.stage is Stage.REVISE_CONFIDENCE:
            revisions = tuple(revise_confidence(
                h, tuple(t for t in p.tests.values() if t.hypothesis_id == h.id), NOW,
            ) for h in p.hypotheses.values())
            revised = tuple(p.hypotheses[r.hypothesis_id].model_copy(update={"current_confidence": r.after}) for r in revisions)
            mutations = (
                *(ConfidenceRevisedPayload(record=r) for r in revisions),
                *(HypothesisStatusChangedPayload(hypothesis_id=h.id, before=p.hypotheses[h.id].status, after=h.status)
                  for h in assign_hypothesis_statuses(revised) if h.status != p.hypotheses[h.id].status),
            )
        await state.persist(job, span, mutations)


@pytest.mark.parametrize("count", (1, 2))
async def test_generation_cannot_advance_without_three_competing_hypotheses(state, count):
    await advance_full_path(state, stop=Stage.GENERATE_HYPOTHESES)
    job, span = await state.claimed()
    run = await state.receipt(job, span)
    p = await state.repository.projection(state.investigation_id)
    mutations = tuple(HypothesisCreatedPayload(
        record=Hypothesis(
            investigation_id=state.investigation_id, case_id=state.case_id, created_at=NOW,
            title=f"Synthetic hypothesis {index}", description="Synthetic incomplete hypothesis set.",
            initial_confidence=Decimal("0.5"), current_confidence=Decimal("0.5"),
            status=HypothesisStatus.ACTIVE, distinguishing_prediction="A measurement is expected.",
            weakening_evidence="An incompatible measurement.", model_run_id=run,
        ), supporting_claim_ids=tuple(p.claims),
    ) for index in range(count))
    before = await state.checkpoint()
    with pytest.raises(ValueError, match="three|3"):
        await state.persist(job, span, mutations)
    assert await state.checkpoint() == before


async def test_empty_generation_is_not_a_deterministic_stage_result(state):
    await advance_full_path(state, stop=Stage.GENERATE_HYPOTHESES)
    job, span = await state.claimed()
    before = await state.checkpoint()
    with pytest.raises(ValueError, match="hypotheses"):
        await state.persist(job, span, ())
    assert await state.checkpoint() == before


@pytest.mark.parametrize("failure", ("evidence", "claim", "timeline", "missing_normalized_test"))
async def test_critique_drafts_require_pinned_internal_refs_and_complete_normalization(state, failure):
    await advance_full_path(state, stop=Stage.DESIGN_FALSIFICATION_TESTS)
    job, span = await state.claimed()
    context = {"investigation_id": state.investigation_id, "case_id": state.case_id, "created_at": NOW}
    draft = HypothesisTestDraft(
        type=Kind.EVIDENCE_PRESENCE, expected_observation="Power recovers", strength=Strength.LOW,
        execution_kind=ExecutionKind.DETERMINISTIC, parameters={"schema_version": "evidence-presence-v1"},
        evidence_ids=(uuid4(),) if failure == "evidence" else state.evidence_ids,
        claim_ids=(uuid4(),) if failure == "claim" else (),
    )
    if failure == "timeline":
        draft = HypothesisTestDraft(**{**draft.model_dump(), "type": Kind.TEMPORAL_CONSISTENCY,
            "parameters": {"schema_version": "temporal-consistency-v1", "before_event_id": str(uuid4()), "after_event_id": str(uuid4())}})
    critique = CritiqueCreatedPayload(record=HypothesisCritique(
        **context, hypothesis_id=UUID(job.work_key), missing_evidence=(), critique_confidence=Decimal("0.5"),
        proposed_tests=(draft,), model_run_id=await state.receipt(job, span),
    ))
    mutations = (critique,)
    if failure != "missing_normalized_test":
        mutations += (CreatedTest(record=HypothesisTest(
            **context, **draft.model_dump(), hypothesis_id=UUID(job.work_key), critique_id=critique.record.id,
            job_id=job.id, status=HypothesisTestStatus.PENDING,
        )),)
    before = await state.checkpoint()
    with pytest.raises(ValueError):
        await state.persist(job, span, mutations)
    assert await state.checkpoint() == before
    assert not (await state.repository.projection(state.investigation_id)).critiques


@pytest.mark.parametrize("failure", ("result_reference", "immutable_input", "deterministic_revision"))
async def test_resolution_and_revision_are_validated_by_typed_state_and_reducer(state, failure):
    stage = Stage.REVISE_CONFIDENCE if failure == "deterministic_revision" else Stage.EXECUTE_FALSIFICATION_TESTS
    await advance_full_path(state, stop=stage)
    job, span = await state.claimed()
    p = await state.repository.projection(state.investigation_id)
    if failure == "deterministic_revision":
        hypothesis = next(iter(p.hypotheses.values()))
        changed = tuple(t.model_copy(update={"outcome": Outcome.CONTRADICTED})
                        for t in p.tests.values() if t.hypothesis_id == hypothesis.id)
        mutation = ConfidenceRevisedPayload(record=revise_confidence(hypothesis, changed, NOW))
    else:
        updates = {"status": HypothesisTestStatus.SUCCEEDED, "outcome": Outcome.SURVIVED, "completed_at": NOW}
        updates.update({"result_evidence_ids": (uuid4(),)} if failure == "result_reference" else {"evidence_ids": ()})
        mutation = ResolvedTest(record=p.tests[UUID(job.work_key)].model_copy(update=updates))
    before = await state.checkpoint()
    with pytest.raises(ValueError):
        await state.persist(job, span, (mutation,))
    assert await state.checkpoint() == before
    assert (await state.repository.projection(state.investigation_id)).canonical_state() == p.canonical_state()


async def test_failed_semantic_test_retains_its_failed_receipt_without_a_confidence_delta(state):
    await advance_full_path(state, stop=Stage.EXECUTE_FALSIFICATION_TESTS, semantic=True)
    job, span = await state.claimed()
    p = await state.repository.projection(state.investigation_id)
    failed = p.tests[UUID(job.work_key)].model_copy(update={
        "status": HypothesisTestStatus.FAILED, "completed_at": NOW,
        "model_run_id": await state.receipt(job, span, "PROVIDER_FAILED"),
    })
    await state.persist(job, span, (ResolvedTest(record=failed),))
    stored = await state.repository.projection(state.investigation_id)
    assert stored.tests[failed.id] == failed
    assert revise_confidence(stored.hypotheses[failed.hypothesis_id], (failed,), NOW).delta == Decimal(0)
    assert stored.canonical_state() == replay(await state.repository.events(state.investigation_id)).canonical_state()


async def test_full_relational_projection_matches_replay_and_completion(state):
    await advance_full_path(state)
    p = await state.repository.projection(state.investigation_id)
    assert all((p.entities, p.timeline, p.claims, p.hypotheses, p.hypothesis_claim_links,
                p.questions, p.critiques, p.tests, p.revisions, p.retrievals))
    assert all(t.result_evidence_ids == state.evidence_ids[1:] and t.evidence_ids == state.evidence_ids[:1]
               for t in p.tests.values())
    assert p.canonical_state() == replay(await state.repository.events(state.investigation_id)).canonical_state()
    job, span = await state.claimed()
    await state.persist(job, span, (InvestigationCompletedPayload(projection_hash=canonical_digest(p.canonical_state())),))
    p = await state.repository.projection(state.investigation_id)
    reconstructed = replay(await state.repository.events(state.investigation_id))
    assert p.canonical_state() == reconstructed.canonical_state()
    assert p.status is reconstructed.status is InvestigationStatus.SUCCEEDED
    assert p.current_stage is reconstructed.current_stage is Stage.COMPLETE
    assert await state.repository.claim(state.investigation_id, "worker") is None


@pytest.mark.parametrize("stage", (Stage.VERIFY_REPLAY, Stage.COMPLETE))
@pytest.mark.parametrize("tamper", ("question", "citation", "retrieval_rank", "delta_order"))
async def test_verification_compares_independent_relational_state(state, stage, tamper):
    await advance_full_path(state, stop=stage)
    p = await state.repository.projection(state.investigation_id)
    job, span = await state.claimed()
    if tamper == "question":
        await state.connection.execute("update unresolved_questions set text='changed' where investigation_id=%s", (state.investigation_id,))
    elif tamper == "citation":
        await state.connection.execute("update hypothesis_tests set result_evidence_ids='{}' where investigation_id=%s", (state.investigation_id,))
    elif tamper == "delta_order":
        await state.connection.execute("update confidence_revision_test_deltas set ordinal=ordinal+2 where investigation_id=%s", (state.investigation_id,))
        await state.connection.execute("update confidence_revision_test_deltas set ordinal=3-ordinal where investigation_id=%s", (state.investigation_id,))
    else:
        await state.connection.execute("reset role")
        async with state.connection.transaction():
            await state.connection.execute("alter table retrieval_results disable trigger retrieval_results_immutable")
            await state.connection.execute("update retrieval_results set rank=rank+2 where investigation_id=%s", (state.investigation_id,))
            await state.connection.execute("update retrieval_results set rank=5-rank where investigation_id=%s", (state.investigation_id,))
            await state.connection.execute("alter table retrieval_results enable trigger retrieval_results_immutable")
        await state.connection.execute("set local role casezero_blind")
    before = await state.checkpoint()
    mutations = () if stage is Stage.VERIFY_REPLAY else (InvestigationCompletedPayload(projection_hash=canonical_digest(p.canonical_state())),)
    with pytest.raises(ValueError):
        await state.persist(job, span, mutations)
    assert await state.checkpoint() == before
