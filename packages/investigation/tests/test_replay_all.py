from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from casezero_investigation.canonical import canonical_digest
from casezero_investigation.confidence import revise_confidence
from casezero_investigation.events import (
    ClaimCreatedPayload,
    ConfidenceRevisedPayload,
    CritiqueCreatedPayload,
    EntityCreatedPayload,
    HypothesisCreatedPayload,
    HypothesisStatusChangedPayload,
    InvestigationCompletedPayload,
    InvestigationEvent,
    InvestigationStartedPayload,
    RetrievalCompletedPayload,
    StageTransitionPayload,
    TimelineCreatedPayload,
)
from casezero_investigation.events import TestCreatedPayload as CreatedTestPayload
from casezero_investigation.events import TestResolvedPayload as ResolvedTestPayload
from casezero_investigation.hypotheses import (
    ConfidenceRevision,
    ExecutionKind,
    HypothesisCritique,
    HypothesisTest,
    HypothesisTestDraft,
    HypothesisTestStatus,
)
from casezero_investigation.hypotheses import TestDelta as Delta
from casezero_investigation.hypotheses import TestOutcome as Outcome
from casezero_investigation.hypotheses import TestStrength as Strength
from casezero_investigation.hypotheses import TestType as FalsificationType
from casezero_investigation.models import (
    HypothesisStatus,
    InvestigationEntity,
    InvestigationStage,
    InvestigationStatus,
    TimelineEvent,
    TimePrecision,
    UnresolvedQuestion,
)
from casezero_investigation.replay import HypothesisClaimLinks, ReplayError, replay
from casezero_retrieval.models import EvidenceSearchQuery, EvidenceSearchResult, RetrievalIntent
from pydantic import ValidationError

from .test_events import CASE_ID, INVESTIGATION_ID, NOW, claim, hypothesis


def test_executor_discovered_citations_replay_without_changing_input_scope(history) -> None:
    from casezero_evidence.models import EvidenceItem, EvidenceType, ExtractionMethod, TextLocator
    from casezero_investigation.falsification import execute_test

    prefix = history[:history.index(find_event(history, "TEST_RESOLVED"))]
    changed = []
    for event in prefix:
        payload = event.payload
        if isinstance(payload, CritiqueCreatedPayload):
            drafts = tuple(
                draft.model_copy(update={"evidence_ids": ()})
                if draft.type is FalsificationType.TEMPORAL_CONSISTENCY else draft
                for draft in payload.record.proposed_tests
            )
            payload = CritiqueCreatedPayload(record=payload.record.model_copy(update={
                "proposed_tests": drafts,
            }))
        elif isinstance(payload, CreatedTestPayload) and payload.record.id == UUID(int=10):
            payload = CreatedTestPayload(record=payload.record.model_copy(update={"evidence_ids": ()}))
        changed.append(event.model_copy(update={"payload": payload}))
    prefix = rechain(changed)
    projected = replay(prefix)
    pending = projected.tests[UUID(int=10)]
    evidence = tuple(
        EvidenceItem(
            id=UUID(int=identifier), case_id=CASE_ID, source_document_id=UUID(int=105),
            type=EvidenceType.TEXT, observation="Synthetic measurement.",
            source_locator=TextLocator(start=0, end=22), extraction_method=ExtractionMethod.DETERMINISTIC,
        )
        for identifier in (103, 104)
    )
    result = execute_test(
        pending, evidence, tuple(projected.claims.values()), tuple(projected.timeline.values()),
        NOW + timedelta(minutes=1),
    )
    applied = replay((*prefix, make_event(ResolvedTestPayload(record=result), prefix[-1])))
    assert result.outcome is Outcome.SURVIVED
    assert applied.tests[pending.id].evidence_ids == ()
    assert applied.tests[pending.id].result_evidence_ids == (UUID(int=103), UUID(int=104))


def test_replay_validation_error_does_not_echo_rejected_values(history) -> None:
    event = history[0].model_copy(update={"unexpected": "rejected-source-payload"})
    with pytest.raises(ReplayError) as failure:
        replay((event,))
    assert "rejected-source-payload" not in str(failure.value)

MODEL_RUN_ID = UUID(int=90)
MISSING_ID = UUID(int=999)
TARGETS = {
    "INVESTIGATION_STARTED": "investigation",
    "STAGE_TRANSITION": "investigation",
    "ENTITY_CREATED": "entity",
    "TIMELINE_CREATED": "timeline_event",
    "CLAIM_CREATED": "claim",
    "HYPOTHESIS_CREATED": "hypothesis",
    "CRITIQUE_CREATED": "critique",
    "TEST_CREATED": "test",
    "TEST_RESOLVED": "test",
    "CONFIDENCE_REVISED": "confidence_revision",
    "HYPOTHESIS_STATUS_CHANGED": "hypothesis",
    "RETRIEVAL_COMPLETED": "retrieval_query",
    "INVESTIGATION_COMPLETED": "investigation",
}


def make_event(payload, previous=None):
    record = getattr(payload, "record", None)
    if record is not None:
        target_id = record.id
    elif isinstance(payload, RetrievalCompletedPayload):
        target_id = payload.query_id
    elif isinstance(payload, HypothesisStatusChangedPayload):
        target_id = payload.hypothesis_id
    else:
        target_id = INVESTIGATION_ID
    event = InvestigationEvent(
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        sequence=previous.sequence + 1 if previous else 1,
        event_type=payload.kind,
        target_type=TARGETS[payload.kind],
        target_id=target_id,
        payload=payload,
        model_run_id=getattr(record, "model_run_id", None),
        previous_event_hash=previous.event_hash if previous else None,
        event_hash="0" * 64,
        hash_algorithm="postgres-investigation-event-v1",
        created_at=NOW,
    )
    return event.model_copy(update={"event_hash": event.computed_hash()})


def rechain(events):
    result = []
    for sequence, event in enumerate(events, start=1):
        event = event.model_copy(update={
            "sequence": sequence,
            "previous_event_hash": result[-1].event_hash if result else None,
        })
        result.append(event.model_copy(update={"event_hash": event.computed_hash()}))
    return tuple(result)


def find_event(events, kind, occurrence=0):
    return tuple(event for event in events if event.event_type == kind)[occurrence]


def replace_payload(events, event, payload):
    index = events.index(event)
    return rechain((*events[:index], event.model_copy(update={"payload": payload})))


def build_history():
    context = {"investigation_id": INVESTIGATION_ID, "case_id": CASE_ID, "created_at": NOW}
    lineage = {
        "source_candidate_ids": (UUID(int=102), UUID(int=101)),
        "evidence_ids": (UUID(int=104), UUID(int=103)),
        "model_run_id": MODEL_RUN_ID,
    }
    entity = InvestigationEntity(
        id=UUID(int=1), type="component", canonical_name="Fuel pump",
        aliases=("pump assembly", " Fuel   Pump "), **context, **lineage,
    )
    before = TimelineEvent(
        id=UUID(int=2), occurred_at=NOW, time_precision=TimePrecision.EXACT,
        description="A pressure change was recorded.", confidence=Decimal("0.8000"),
        **context, **lineage,
    )
    after = before.model_copy(update={
        "id": UUID(int=3), "occurred_at": NOW + timedelta(seconds=5),
        "description": "A power change was recorded.",
    })
    supporting = claim().model_copy(update={"id": UUID(int=4), "model_run_id": MODEL_RUN_ID})
    contradicting = claim().model_copy(update={
        "id": UUID(int=5), "text": "The available pressure measurement was normal.",
        "model_run_id": MODEL_RUN_ID,
    })
    proposed = hypothesis().model_copy(update={"id": UUID(int=6), "model_run_id": MODEL_RUN_ID})
    questions = tuple(
        UnresolvedQuestion(
            id=UUID(int=identifier), hypothesis_id=proposed.id, text=text, **context,
        )
        for identifier, text in (
            (8, "Was the pressure measurement independent?"),
            (7, "Is a flow measurement available?"),
        )
    )
    temporal = HypothesisTestDraft(
        type=FalsificationType.TEMPORAL_CONSISTENCY,
        expected_observation="The pressure change precedes the power change.",
        strength=Strength.MEDIUM, execution_kind=ExecutionKind.DETERMINISTIC,
        parameters={
            "schema_version": "temporal-consistency-v1",
            "before_event_id": str(before.id), "after_event_id": str(after.id),
        },
        evidence_ids=(UUID(int=104), UUID(int=103)),
        claim_ids=(contradicting.id, supporting.id),
    )
    semantic = HypothesisTestDraft(
        type=FalsificationType.SEMANTIC_COMPARISON,
        expected_observation="Independent measurements describe comparable operating conditions.",
        strength=Strength.LOW, execution_kind=ExecutionKind.AI,
        parameters={"schema_version": "semantic-comparison-v1"},
        evidence_ids=(UUID(int=103),), claim_ids=(supporting.id,),
    )
    critique = HypothesisCritique(
        id=UUID(int=9), hypothesis_id=proposed.id,
        strongest_contradiction_id=contradicting.id,
        missing_evidence=("Independent flow data", "A calibration record"),
        alternative_explanation="The measurements may cover different operating conditions.",
        critique_confidence=Decimal("0.6000"), proposed_tests=(temporal, semantic),
        model_run_id=MODEL_RUN_ID, **context,
    )
    pending = HypothesisTest(
        id=UUID(int=10), hypothesis_id=proposed.id, critique_id=critique.id, job_id=UUID(int=92),
        status=HypothesisTestStatus.PENDING, **temporal.model_dump(), **context,
    )
    pending_ai = HypothesisTest(
        id=UUID(int=11), hypothesis_id=proposed.id, critique_id=critique.id, job_id=UUID(int=93),
        status=HypothesisTestStatus.PENDING, **semantic.model_dump(), **context,
    )
    resolved = pending.model_copy(update={
        "status": HypothesisTestStatus.SUCCEEDED, "outcome": Outcome.CONTRADICTED,
        "completed_at": NOW + timedelta(minutes=1),
    })
    failed = pending_ai.model_copy(update={
        "status": HypothesisTestStatus.FAILED, "model_run_id": UUID(int=91),
        "completed_at": NOW + timedelta(minutes=1),
    })
    revision = revise_confidence(proposed, (resolved, failed), NOW).model_copy(update={
        "id": UUID(int=12),
    })
    query = EvidenceSearchQuery(
        investigation_id=INVESTIGATION_ID, case_id=CASE_ID, intent=RetrievalIntent.SUPPORT,
        query_text="fuel pressure", entity_ids=(entity.id,), start_at=NOW, limit=2,
    )
    results = tuple(
        EvidenceSearchResult(
            evidence_id=UUID(int=identifier), rank=rank,
            fts_score=Decimal("0.5000"), entity_score=Decimal("1.00"), time_score=Decimal(1),
            type_score=Decimal(0), total_score=Decimal("0.6250"), matched_filters=("time", "entity"),
        )
        for rank, identifier in ((1, 104), (2, 103))
    )
    payloads = (
        InvestigationStartedPayload(
            configuration_hash="a" * 64, current_stage=InvestigationStage.PROMOTE_TIMELINE,
            status=InvestigationStatus.PENDING,
        ),
        TimelineCreatedPayload(record=before), TimelineCreatedPayload(record=after),
        StageTransitionPayload(
            previous_stage=InvestigationStage.PROMOTE_TIMELINE,
            next_stage=InvestigationStage.RESOLVE_ENTITIES,
        ),
        EntityCreatedPayload(record=entity),
        StageTransitionPayload(
            previous_stage=InvestigationStage.RESOLVE_ENTITIES,
            next_stage=InvestigationStage.PROMOTE_CLAIMS,
        ),
        ClaimCreatedPayload(record=supporting), ClaimCreatedPayload(record=contradicting),
        StageTransitionPayload(
            previous_stage=InvestigationStage.PROMOTE_CLAIMS,
            next_stage=InvestigationStage.GENERATE_HYPOTHESES,
        ),
        HypothesisCreatedPayload(
            record=proposed, supporting_claim_ids=(supporting.id,),
            contradicting_claim_ids=(contradicting.id,), questions=questions,
        ),
        StageTransitionPayload(
            previous_stage=InvestigationStage.GENERATE_HYPOTHESES,
            next_stage=InvestigationStage.SEARCH_SUPPORT,
        ),
        RetrievalCompletedPayload(
            query_id=UUID(int=13), hypothesis_id=proposed.id, query=query, results=results,
        ),
        StageTransitionPayload(
            previous_stage=InvestigationStage.SEARCH_SUPPORT,
            next_stage=InvestigationStage.SEARCH_CONTRADICTIONS,
        ),
        RetrievalCompletedPayload(
            query_id=UUID(int=14), hypothesis_id=proposed.id,
            query=query.model_copy(update={"intent": RetrievalIntent.CONTRADICT}), results=(),
        ),
        StageTransitionPayload(
            previous_stage=InvestigationStage.SEARCH_CONTRADICTIONS,
            next_stage=InvestigationStage.DESIGN_FALSIFICATION_TESTS,
        ),
        CritiqueCreatedPayload(record=critique),
        CreatedTestPayload(record=pending), CreatedTestPayload(record=pending_ai),
        StageTransitionPayload(
            previous_stage=InvestigationStage.DESIGN_FALSIFICATION_TESTS,
            next_stage=InvestigationStage.EXECUTE_FALSIFICATION_TESTS,
        ),
        ResolvedTestPayload(record=resolved), ResolvedTestPayload(record=failed),
        StageTransitionPayload(
            previous_stage=InvestigationStage.EXECUTE_FALSIFICATION_TESTS,
            next_stage=InvestigationStage.REVISE_CONFIDENCE,
        ),
        ConfidenceRevisedPayload(record=revision),
        HypothesisStatusChangedPayload(
            hypothesis_id=proposed.id, before=HypothesisStatus.ACTIVE, after=HypothesisStatus.WEAKENED,
        ),
        StageTransitionPayload(
            previous_stage=InvestigationStage.REVISE_CONFIDENCE,
            next_stage=InvestigationStage.VERIFY_REPLAY,
        ),
        StageTransitionPayload(
            previous_stage=InvestigationStage.VERIFY_REPLAY,
            next_stage=InvestigationStage.COMPLETE,
        ),
    )
    events = []
    for payload in payloads:
        events.append(make_event(payload, events[-1] if events else None))
    state_hash = canonical_digest(replay(tuple(events)).canonical_state())
    events.append(make_event(InvestigationCompletedPayload(projection_hash=state_hash), events[-1]))
    return tuple(events)


@pytest.fixture
def history():
    return build_history()


def test_complete_chain_reconstructs_every_canonical_record_and_relation(history) -> None:
    snapshots = tuple(event.model_dump() for event in history)
    projection = replay(history)
    assert {event.event_type for event in history} == set(TARGETS)
    for kind, collection in (
        ("ENTITY_CREATED", projection.entities), ("TIMELINE_CREATED", projection.timeline),
        ("CLAIM_CREATED", projection.claims), ("CRITIQUE_CREATED", projection.critiques),
        ("TEST_RESOLVED", projection.tests), ("CONFIDENCE_REVISED", projection.revisions),
    ):
        expected = {
            event.payload.record.id: event.payload.record
            for event in history if event.event_type == kind
        }
        assert collection == expected
    proposed = find_event(history, "HYPOTHESIS_CREATED").payload
    revised = find_event(history, "CONFIDENCE_REVISED").payload.record
    assert projection.hypotheses == {
        proposed.record.id: proposed.record.model_copy(update={
            "current_confidence": revised.after, "status": HypothesisStatus.WEAKENED,
        }),
    }
    assert projection.questions == {question.id: question for question in proposed.questions}
    assert projection.hypothesis_claim_links == {
        proposed.record.id: HypothesisClaimLinks(
            supporting=proposed.supporting_claim_ids, contradicting=proposed.contradicting_claim_ids,
        ),
    }
    assert projection.retrievals == {
        event.payload.query_id: event.payload
        for event in history if event.event_type == "RETRIEVAL_COMPLETED"
    }
    assert projection.status is InvestigationStatus.SUCCEEDED
    assert projection.current_stage is InvestigationStage.COMPLETE
    assert projection.event_head_hash == history[-1].event_hash
    assert canonical_digest(projection.canonical_state()) == history[-1].payload.projection_hash
    assert replay(history) == projection
    assert tuple(event.model_dump() for event in history) == snapshots


def test_payload_versions_and_discriminated_union_are_closed(history) -> None:
    for event in history:
        payload = event.payload
        assert payload.schema_version == "1"
        assert InvestigationEvent.model_validate_json(event.model_dump_json()) == event
        for change in ({"schema_version": "2"}, {"unexpected": "field"}, {"schema_version": 1}):
            with pytest.raises(ValidationError):
                type(payload).model_validate(payload.model_dump() | change)
        with pytest.raises(ValidationError, match="event_type|payload"):
            InvestigationEvent.model_validate(event.model_dump() | {"event_type": "UNKNOWN"})
        other = "CLAIM_CREATED" if event.event_type != "CLAIM_CREATED" else "HYPOTHESIS_CREATED"
        with pytest.raises(ValidationError, match="payload kind"):
            InvestigationEvent.model_validate(event.model_dump() | {"event_type": other})


@pytest.mark.parametrize("field", ("target_type", "target_id"))
def test_every_event_requires_its_exact_target(history, field) -> None:
    for event in history:
        with pytest.raises(ValidationError, match="target"):
            InvestigationEvent.model_validate(event.model_dump() | {
                field: "unsupported" if field == "target_type" else MISSING_ID,
            })


@pytest.mark.parametrize("field", ("investigation_id", "case_id"))
def test_record_and_query_context_must_match_the_event(history, field) -> None:
    for event in history:
        payload = event.payload.model_dump()
        context_record = payload.get("record", payload.get("query"))
        if context_record is not None:
            context_record[field] = MISSING_ID
            with pytest.raises(ValidationError, match="context"):
                InvestigationEvent.model_validate(event.model_dump() | {"payload": payload})


def test_event_attribution_matches_records_and_deterministic_lifecycle(history) -> None:
    for event in history:
        if isinstance(event.payload, RetrievalCompletedPayload):
            attributed = InvestigationEvent.model_validate(event.model_dump() | {
                "model_run_id": MODEL_RUN_ID,
            })
            assert attributed.model_run_id == MODEL_RUN_ID
            continue
        for model_run_id in (MISSING_ID, None):
            if model_run_id == event.model_run_id:
                continue
            with pytest.raises(ValidationError, match="model_run_id"):
                InvestigationEvent.model_validate(event.model_dump() | {"model_run_id": model_run_id})


@pytest.mark.parametrize("field", ("investigation_id", "case_id", "hypothesis_id"))
def test_question_context_belongs_to_the_created_hypothesis(history, field) -> None:
    payload = find_event(history, "HYPOTHESIS_CREATED").payload
    question = payload.questions[0].model_copy(update={field: MISSING_ID})
    with pytest.raises(ValidationError, match="question.*context"):
        HypothesisCreatedPayload.model_validate(payload.model_dump() | {"questions": (question,)})


def test_set_like_payload_fields_are_ordered_without_mutating_source_records(history) -> None:
    entity = find_event(history, "ENTITY_CREATED").payload.record
    assert entity.aliases == (" Fuel   Pump ", "pump assembly")
    assert entity.source_candidate_ids == (UUID(int=101), UUID(int=102))
    assert entity.evidence_ids == (UUID(int=103), UUID(int=104))
    proposed = find_event(history, "HYPOTHESIS_CREATED").payload
    assert tuple(question.id for question in proposed.questions) == (UUID(int=7), UUID(int=8))
    claimed = find_event(history, "CLAIM_CREATED").payload.record
    unordered = claimed.model_copy(update={
        "source_candidate_ids": (UUID(int=102), UUID(int=101)),
        "supporting_evidence_ids": (UUID(int=104), UUID(int=103)),
    })
    normalized = ClaimCreatedPayload(record=unordered)
    assert unordered.source_candidate_ids == (UUID(int=102), UUID(int=101))
    assert normalized.record.source_candidate_ids == entity.source_candidate_ids
    assert normalized.record.supporting_evidence_ids == entity.evidence_ids
    forward = make_event(normalized, history[0])
    backward = make_event(ClaimCreatedPayload(record=normalized.record), history[0])
    assert forward.computed_hash() == backward.computed_hash()
    linked = HypothesisCreatedPayload(
        record=proposed.record, supporting_claim_ids=(UUID(int=5), UUID(int=4)),
    )
    assert linked.supporting_claim_ids == (UUID(int=4), UUID(int=5))
    tested = find_event(history, "TEST_CREATED").payload.record
    assert tested.claim_ids == (UUID(int=4), UUID(int=5))
    assert tested.evidence_ids == entity.evidence_ids
    critique = find_event(history, "CRITIQUE_CREATED").payload.record
    assert critique.missing_evidence == ("A calibration record", "Independent flow data")


@pytest.mark.parametrize(("kind", "field"), (
    ("ENTITY_CREATED", "source_candidate_ids"), ("ENTITY_CREATED", "evidence_ids"),
    ("TIMELINE_CREATED", "source_candidate_ids"), ("TIMELINE_CREATED", "evidence_ids"),
    ("CLAIM_CREATED", "source_candidate_ids"), ("CLAIM_CREATED", "supporting_evidence_ids"),
    ("CLAIM_CREATED", "contradicting_evidence_ids"),
    ("TEST_CREATED", "evidence_ids"), ("TEST_CREATED", "claim_ids"),
))
def test_record_reference_arrays_reject_duplicate_ids(history, kind, field) -> None:
    payload = find_event(history, kind).payload
    record = payload.record.model_copy(update={field: (UUID(int=101), UUID(int=101))})
    with pytest.raises(ValidationError, match="unique|duplicate"):
        type(payload)(record=record)


def test_hypothesis_and_text_sets_reject_duplicates(history) -> None:
    proposed = find_event(history, "HYPOTHESIS_CREATED").payload
    for update in (
        {"supporting_claim_ids": (UUID(int=4), UUID(int=4))},
        {"contradicting_claim_ids": (UUID(int=5), UUID(int=5))},
        {"questions": (proposed.questions[0], proposed.questions[0])},
        {"questions": (
            proposed.questions[0], proposed.questions[0].model_copy(update={"id": MISSING_ID}),
        )},
    ):
        with pytest.raises(ValidationError, match="unique|duplicate"):
            HypothesisCreatedPayload.model_validate(proposed.model_dump() | update)
    entity = find_event(history, "ENTITY_CREATED").payload.record
    with pytest.raises(ValidationError, match="unique|duplicate"):
        EntityCreatedPayload(record=entity.model_copy(update={"aliases": ("fuel pump", " FUEL  PUMP ")}))


def test_retrieval_preserves_rank_not_evidence_id_order(history) -> None:
    payload = find_event(history, "RETRIEVAL_COMPLETED").payload
    assert tuple(result.evidence_id for result in payload.results) == (UUID(int=104), UUID(int=103))
    assert payload.results[0].matched_filters == ("entity", "time")
    bad_results = (
        tuple(reversed(payload.results)),
        (payload.results[0].model_copy(update={"rank": 2}),),
        (payload.results[0], payload.results[1].model_copy(update={"rank": 1})),
        (payload.results[0], payload.results[1].model_copy(update={"evidence_id": UUID(int=104)})),
        (payload.results[0].model_copy(update={"matched_filters": ("time", "time")}),),
    )
    for results in bad_results:
        with pytest.raises(ValidationError, match="rank|unique|duplicate"):
            RetrievalCompletedPayload.model_validate(payload.model_dump() | {"results": results})
    with pytest.raises(ValidationError, match="limit"):
        RetrievalCompletedPayload.model_validate(payload.model_dump() | {
            "query": payload.query.model_copy(update={"limit": 1}),
        })
    assert find_event(history, "RETRIEVAL_COMPLETED", 1).payload.results == ()


def test_pending_tests_cannot_claim_result_citations(history) -> None:
    pending = find_event(history, "TEST_CREATED").payload.record
    with pytest.raises(ValidationError, match="pending|result"):
        HypothesisTest.model_validate(pending.model_dump() | {"result_evidence_ids": (UUID(int=103),)})


def test_test_creation_and_resolution_payloads_enforce_status(history) -> None:
    pending = find_event(history, "TEST_CREATED").payload.record
    resolved = find_event(history, "TEST_RESOLVED").payload.record
    with pytest.raises(ValidationError, match="PENDING"):
        CreatedTestPayload(record=resolved)
    with pytest.raises(ValidationError, match="SUCCEEDED|FAILED|resolved"):
        ResolvedTestPayload(record=pending)
    with pytest.raises(ValidationError, match="change|different"):
        HypothesisStatusChangedPayload(
            hypothesis_id=pending.hypothesis_id, before=HypothesisStatus.ACTIVE,
            after=HypothesisStatus.ACTIVE,
        )


def test_replay_checks_every_event_digest_including_first(history) -> None:
    for index, event in enumerate(history):
        tampered = event.model_copy(update={"event_hash": "f" * 64})
        with pytest.raises(ReplayError, match="digest"):
            replay((*history[:index], tampered))
    claimed = find_event(history, "CLAIM_CREATED")
    tampered = claimed.model_copy(update={"payload": claimed.payload.model_copy(update={
        "record": claimed.payload.record.model_copy(update={"text": "Altered observation."}),
    })})
    with pytest.raises(ReplayError, match="digest"):
        replay((*history[:history.index(claimed)], tampered))


def test_replay_revalidates_resigned_invalid_payloads(history) -> None:
    event = find_event(history, "CLAIM_CREATED")
    for update in ({"schema_version": "2"}, {"kind": "HYPOTHESIS_CREATED"}):
        payload = event.payload.model_copy(update=update)
        with pytest.raises(ReplayError, match="invalid|payload"):
            replay(replace_payload(history, event, payload))
    forged = event.model_copy(update={"target_id": MISSING_ID})
    with pytest.raises(ReplayError, match="invalid|target"):
        replay(rechain((*history[:history.index(event)], forged)))


def test_replay_requires_start_contiguous_sequence_context_and_unique_event_ids(history) -> None:
    with pytest.raises(ReplayError, match="requires events"):
        replay(())
    with pytest.raises(ReplayError, match="start"):
        replay(rechain((history[1],)))
    with pytest.raises(ReplayError, match="sequence"):
        replay((history[0], history[2]))
    with pytest.raises(ReplayError, match="previous hash"):
        replay((history[0], history[1].model_copy(update={"previous_event_hash": "f" * 64})))
    with pytest.raises(ReplayError, match="duplicate.*event"):
        replay((history[0], history[1].model_copy(update={"id": history[0].id})))
    stage = find_event(history, "STAGE_TRANSITION")
    for field in ("case_id", "investigation_id"):
        changed = stage.model_copy(update={field: MISSING_ID})
        if field == "investigation_id":
            changed = changed.model_copy(update={"target_id": MISSING_ID})
        with pytest.raises(ReplayError, match="context"):
            replay(rechain((*history[:history.index(stage)], changed)))
    repeated = history[0].model_copy(update={"id": uuid4()})
    with pytest.raises(ReplayError, match="start"):
        replay(rechain((history[0], repeated)))


@pytest.mark.parametrize("kind", (
    "ENTITY_CREATED", "TIMELINE_CREATED", "CLAIM_CREATED", "HYPOTHESIS_CREATED",
    "CRITIQUE_CREATED", "TEST_CREATED", "CONFIDENCE_REVISED", "RETRIEVAL_COMPLETED",
))
def test_replay_rejects_duplicate_record_creation(history, kind) -> None:
    event = find_event(history, kind)
    index = history.index(event)
    duplicate = event.model_copy(update={"id": uuid4()})
    with pytest.raises(ReplayError, match="duplicate"):
        replay(rechain((*history[:index + 1], duplicate)))


@pytest.mark.parametrize(("kind", "field"), (
    ("CRITIQUE_CREATED", "hypothesis_id"), ("TEST_CREATED", "hypothesis_id"),
    ("TEST_CREATED", "critique_id"), ("CONFIDENCE_REVISED", "hypothesis_id"),
))
def test_replay_rejects_missing_record_parent_references(history, kind, field) -> None:
    event = find_event(history, kind)
    payload = event.payload.model_copy(update={
        "record": event.payload.record.model_copy(update={field: MISSING_ID}),
    })
    with pytest.raises(ReplayError, match="unknown|reference|before creation"):
        replay(replace_payload(history, event, payload))


def test_replay_requires_created_claim_links_and_unique_question_ids(history) -> None:
    event = find_event(history, "HYPOTHESIS_CREATED")
    for field in ("supporting_claim_ids", "contradicting_claim_ids"):
        with pytest.raises(ReplayError, match="claim"):
            replay(replace_payload(history, event, event.payload.model_copy(update={field: (MISSING_ID,)})))
    first = event.payload
    second_record = first.record.model_copy(update={"id": UUID(int=20)})
    duplicate_question = first.questions[0].model_copy(update={"hypothesis_id": second_record.id})
    second = HypothesisCreatedPayload(
        record=second_record, supporting_claim_ids=first.supporting_claim_ids,
        questions=(duplicate_question,),
    )
    prefix = history[:history.index(event) + 1]
    with pytest.raises(ReplayError, match="duplicate.*question"):
        replay((*prefix, make_event(second, prefix[-1])))


@pytest.mark.parametrize("field", ("claim_ids", "before_event_id", "after_event_id"))
@pytest.mark.parametrize("kind", ("CRITIQUE_CREATED", "TEST_CREATED"))
def test_nested_test_references_must_already_exist(history, kind, field) -> None:
    event = find_event(history, kind)
    record = event.payload.record
    draft = find_event(history, "TEST_CREATED").payload.record
    change = (
        {"claim_ids": (MISSING_ID,)} if field == "claim_ids"
        else {"parameters": draft.parameters | {field: str(MISSING_ID)}}
    )
    if kind == "CRITIQUE_CREATED":
        draft_fields = HypothesisTestDraft.model_fields
        draft = HypothesisTestDraft.model_validate({
            key: value for key, value in draft.model_dump().items() if key in draft_fields
        }).model_copy(update=change)
        record = record.model_copy(update={"proposed_tests": (draft,)})
    else:
        record = record.model_copy(update=change)
    with pytest.raises(ReplayError, match="claim|timeline"):
        replay(replace_payload(history, event, event.payload.model_copy(update={"record": record})))


@pytest.mark.parametrize("field", ("hypothesis_id", "entity_ids"))
def test_retrieval_requires_created_hypothesis_and_filter_entities(history, field) -> None:
    event = find_event(history, "RETRIEVAL_COMPLETED")
    payload = event.payload
    update = (
        {"hypothesis_id": MISSING_ID} if field == "hypothesis_id"
        else {"query": payload.query.model_copy(update={"entity_ids": (MISSING_ID,)})}
    )
    with pytest.raises(ReplayError, match="hypothesis|entit"):
        replay(replace_payload(history, event, payload.model_copy(update=update)))


def test_resolution_requires_pending_creation_and_cannot_resolve_twice(history) -> None:
    event = find_event(history, "TEST_RESOLVED")
    before_create = history[:history.index(find_event(history, "TEST_CREATED"))]
    with pytest.raises(ReplayError, match="test|before creation"):
        replay(rechain((*before_create, event)))
    resolved_prefix = history[:history.index(event) + 1]
    with pytest.raises(ReplayError, match="PENDING|pending"):
        replay(rechain((*resolved_prefix, event.model_copy(update={"id": uuid4()}))))


@pytest.mark.parametrize(("field", "value"), (
    ("job_id", MISSING_ID), ("expected_observation", "A replacement prediction."),
    ("strength", Strength.HIGH), ("created_at", NOW - timedelta(seconds=1)),
    ("evidence_ids", (MISSING_ID,)), ("claim_ids", (UUID(int=4),)),
))
def test_resolution_cannot_rewrite_the_pending_test(history, field, value) -> None:
    event = find_event(history, "TEST_RESOLVED")
    payload = event.payload.model_copy(update={
        "record": event.payload.record.model_copy(update={field: value}),
    })
    with pytest.raises(ReplayError, match="immutable|changed|match"):
        replay(replace_payload(history, event, payload))


def test_test_and_critique_must_reference_the_same_hypothesis(history) -> None:
    event = find_event(history, "TEST_CREATED")
    original = find_event(history, "HYPOTHESIS_CREATED").payload
    another = HypothesisCreatedPayload(
        record=original.record.model_copy(update={"id": UUID(int=20)}),
        supporting_claim_ids=original.supporting_claim_ids,
    )
    prefix = history[:history.index(event)]
    prefix = (*prefix, make_event(another, prefix[-1]))
    changed = event.model_copy(update={"payload": event.payload.model_copy(update={
        "record": event.payload.record.model_copy(update={"hypothesis_id": another.record.id}),
    })})
    with pytest.raises(ReplayError, match="hypothesis"):
        replay(rechain((*prefix, changed)))


def test_revision_requires_matching_before_and_successful_owned_test_deltas(history) -> None:
    event = find_event(history, "CONFIDENCE_REVISED")
    revision = event.payload.record
    changes = (
        {"before": Decimal("0.3900"), "after": Decimal("0.2100")},
        {"test_deltas": (Delta(test_id=MISSING_ID, delta=Decimal("-0.1800")),)},
        {"test_deltas": (Delta(test_id=UUID(int=11), delta=Decimal("-0.1800")),)},
        {
            "delta": Decimal("-0.0800"), "after": Decimal("0.3000"),
            "test_deltas": (Delta(test_id=UUID(int=10), delta=Decimal("-0.0800")),),
        },
    )
    for change in changes:
        payload = ConfidenceRevisedPayload(record=ConfidenceRevision.model_validate(
            revision.model_dump() | change,
        ))
        with pytest.raises(ReplayError, match="before|test|delta|SUCCEEDED"):
            replay(replace_payload(history, event, payload))
    before_result = history[:history.index(find_event(history, "TEST_RESOLVED"))]
    with pytest.raises(ReplayError, match="SUCCEEDED|pending"):
        replay(rechain((*before_result, event)))
    original = find_event(history, "HYPOTHESIS_CREATED").payload
    another = HypothesisCreatedPayload(
        record=original.record.model_copy(update={"id": UUID(int=20)}),
        supporting_claim_ids=original.supporting_claim_ids,
    )
    prefix = history[:history.index(event)]
    prefix = (*prefix, make_event(another, prefix[-1]))
    changed = event.model_copy(update={"payload": event.payload.model_copy(update={
        "record": revision.model_copy(update={"hypothesis_id": another.record.id}),
    })})
    with pytest.raises(ReplayError, match="hypothesis"):
        replay(rechain((*prefix, changed)))


def test_status_change_requires_existing_hypothesis_and_matching_before(history) -> None:
    event = find_event(history, "HYPOTHESIS_STATUS_CHANGED")
    for update in ({"hypothesis_id": MISSING_ID}, {"before": HypothesisStatus.LEADING}):
        payload = event.payload.model_copy(update=update)
        changed = event.model_copy(update={"payload": payload, "target_id": payload.hypothesis_id})
        with pytest.raises(ReplayError, match="hypothesis|before"):
            replay(rechain((*history[:history.index(event)], changed)))


def test_stage_order_start_status_and_terminal_boundary(history) -> None:
    first = history[0]
    for update in (
        {"status": InvestigationStatus.SUCCEEDED},
        {"current_stage": InvestigationStage.COMPLETE},
    ):
        with pytest.raises(ValidationError, match="PENDING|PROMOTE_TIMELINE|start"):
            InvestigationStartedPayload.model_validate(first.payload.model_dump() | update)
    same = StageTransitionPayload(
        previous_stage=InvestigationStage.PROMOTE_TIMELINE,
        next_stage=InvestigationStage.PROMOTE_TIMELINE,
    )
    began = make_event(same, first)
    assert replay((first, began)).status is InvestigationStatus.RUNNING
    with pytest.raises(ReplayError, match="stage|start"):
        replay((first, began, make_event(same, began)))
    for previous, following in (
        (InvestigationStage.PROMOTE_TIMELINE, InvestigationStage.PROMOTE_CLAIMS),
        (InvestigationStage.RESOLVE_ENTITIES, InvestigationStage.PROMOTE_TIMELINE),
        (InvestigationStage.RESOLVE_ENTITIES, InvestigationStage.RESOLVE_ENTITIES),
        (InvestigationStage.COMPLETE, InvestigationStage.PROMOTE_TIMELINE),
    ):
        with pytest.raises(ValidationError, match="order|stage"):
            StageTransitionPayload(previous_stage=previous, next_stage=following)
    wrong_start = StageTransitionPayload(
        previous_stage=InvestigationStage.RESOLVE_ENTITIES,
        next_stage=InvestigationStage.PROMOTE_CLAIMS,
    )
    with pytest.raises(ReplayError, match="stage"):
        replay((first, make_event(wrong_start, first)))
    with pytest.raises(ReplayError, match="COMPLETE|stage"):
        replay((first, make_event(history[-1].payload, first)))
    with pytest.raises(ReplayError, match="complet|terminal"):
        replay((*history, make_event(history[1].payload, history[-1])))
    with pytest.raises(ReplayError, match="complet|terminal"):
        replay((*history, make_event(history[-1].payload, history[-1])))


def test_completion_verifies_canonical_projection_not_lifecycle_metadata(history) -> None:
    completed = history[-1]
    with pytest.raises(ReplayError, match="projection"):
        replay(replace_payload(history, completed, InvestigationCompletedPayload(projection_hash="f" * 64)))
    before = replay(history[:-1])
    after = replay(history)
    assert before.status is InvestigationStatus.RUNNING
    assert before.canonical_state() == after.canonical_state()
    altered = after.model_copy(update={
        "event_head_hash": "b" * 64, "configuration_hash": "c" * 64,
        "status": InvestigationStatus.PENDING, "current_stage": InvestigationStage.PROMOTE_TIMELINE,
    })
    assert altered.canonical_state() == after.canonical_state()
    canonical = after.canonical_state()
    assert set(canonical) == {
        "investigation_id", "case_id", "entities", "timeline", "claims", "hypotheses",
        "questions", "critiques", "tests", "revisions", "retrievals", "hypothesis_claim_links",
    }
    assert canonical["hypotheses"][str(UUID(int=6))]["status"] == "WEAKENED"
    assert canonical["hypotheses"][str(UUID(int=6))]["current_confidence"] == "0.2"
    assert canonical["hypothesis_claim_links"][str(UUID(int=6))] == {
        "supporting": [str(UUID(int=4))], "contradicting": [str(UUID(int=5))],
    }
    assert set(canonical["retrievals"][str(UUID(int=13))]) == {
        "query_id", "hypothesis_id", "query", "results",
    }


def test_canonical_state_normalizes_relational_order_decimal_scale_and_database_times(history) -> None:
    projection = replay(history)
    update = {}
    for field in ("entities", "timeline", "claims", "hypotheses", "questions", "critiques", "tests", "revisions"):
        records = {}
        for identifier, record in reversed(tuple(getattr(projection, field).items())):
            values = {"created_at": record.created_at + timedelta(days=1)}
            if getattr(record, "completed_at", None) is not None:
                values["completed_at"] = record.completed_at + timedelta(days=1)
            for name in (
                "source_candidate_ids", "evidence_ids", "supporting_evidence_ids",
                "contradicting_evidence_ids", "claim_ids", "aliases", "missing_evidence", "proposed_tests",
            ):
                if hasattr(record, name):
                    values[name] = tuple(reversed(getattr(record, name)))
            records[identifier] = record.model_copy(update=values)
        update[field] = records
    retrievals = {}
    for identifier, payload in reversed(tuple(projection.retrievals.items())):
        results = tuple(result.model_copy(update={
            "fts_score": Decimal("0.5"), "total_score": Decimal("0.625000"),
            "matched_filters": tuple(reversed(result.matched_filters)),
        }) for result in payload.results)
        retrievals[identifier] = payload.model_copy(update={"results": results})
    update["retrievals"] = retrievals
    assert projection.model_copy(update=update).canonical_state() == projection.canonical_state()
    canonical = projection.canonical_state()
    assert "created_at" not in str(canonical)
    assert "completed_at" not in str(canonical)
    assert canonical["timeline"][str(UUID(int=2))]["occurred_at"] == "2026-09-05T00:00:00Z"
    timeline = dict(projection.timeline)
    timeline[UUID(int=2)] = timeline[UUID(int=2)].model_copy(update={"occurred_at": None})
    assert projection.model_copy(update={"timeline": timeline}).canonical_state() != canonical
    payload = projection.retrievals[UUID(int=13)]
    changed = payload.model_copy(update={"query": payload.query.model_copy(update={"start_at": None})})
    assert projection.model_copy(update={
        "retrievals": projection.retrievals | {payload.query_id: changed},
    }).canonical_state() != canonical
    changed = payload.model_copy(update={"results": tuple(reversed(payload.results))})
    assert projection.model_copy(update={
        "retrievals": projection.retrievals | {payload.query_id: changed},
    }).canonical_state() != canonical


def test_equivalent_create_order_changes_event_chain_but_not_projection(history) -> None:
    first = history.index(find_event(history, "CLAIM_CREATED"))
    reordered = list(history)
    reordered[first], reordered[first + 1] = reordered[first + 1], reordered[first]
    changed = rechain(reordered)
    assert changed[-1].event_hash != history[-1].event_hash
    assert replay(changed).canonical_state() == replay(history).canonical_state()


def test_revision_rejects_rehashed_falsified_rationale(history) -> None:
    event = find_event(history, "CONFIDENCE_REVISED")
    record = event.payload.record.model_copy(update={
        "rationale": "A medium-strength temporal test contradicted the predicted ordering.",
    })
    with pytest.raises(ReplayError, match="rationale"):
        replay(replace_payload(history, event, ConfidenceRevisedPayload(record=record)))


def test_revision_cannot_omit_succeeded_tests_or_repeat_the_cycle(history) -> None:
    event = find_event(history, "CONFIDENCE_REVISED")
    hypothesis = find_event(history, "HYPOTHESIS_CREATED").payload.record
    omitted = revise_confidence(hypothesis, (), NOW).model_copy(update={"id": event.target_id})
    with pytest.raises(ReplayError, match="SUCCEEDED|test"):
        replay(replace_payload(history, event, ConfidenceRevisedPayload(record=omitted)))
    prefix = history[:history.index(event) + 1]
    revised = replay(prefix).hypotheses[hypothesis.id]
    repeated = revise_confidence(
        revised, (find_event(history, "TEST_RESOLVED").payload.record,), NOW,
    ).model_copy(update={"id": UUID(int=30)})
    with pytest.raises(ReplayError, match="duplicate|already|cycle"):
        replay((*prefix, make_event(ConfidenceRevisedPayload(record=repeated), prefix[-1])))


@pytest.mark.parametrize(("kind", "field", "value"), (
    ("CLAIM_CREATED", "confidence", Decimal("1.0001")),
    ("ENTITY_CREATED", "aliases", (" ",)),
    ("TIMELINE_CREATED", "occurred_at", NOW.replace(tzinfo=None)),
    ("TEST_RESOLVED", "outcome", None),
    ("CONFIDENCE_REVISED", "after", Decimal("0.2100")),
))
def test_replay_revalidates_rehashed_nested_records(history, kind, field, value) -> None:
    event = find_event(history, kind)
    payload = event.payload.model_copy(update={
        "record": event.payload.record.model_copy(update={field: value}),
    })
    with pytest.raises(ReplayError, match="invalid"):
        replay(replace_payload(history, event, payload))


@pytest.mark.parametrize(("field", "value"), (
    ("hash_algorithm", "unsupported"), ("created_at", NOW.replace(tzinfo=None)),
    ("model_run_id", MISSING_ID),
))
def test_replay_revalidates_rehashed_event_envelopes(history, field, value) -> None:
    changed = history[0].model_copy(update={field: value})
    with pytest.raises(ReplayError, match="invalid"):
        replay(rechain((changed,)))


@pytest.mark.parametrize("level", ("event", "payload", "record", "test_delta", "test_draft"))
def test_replay_rejects_extra_fields_hidden_by_model_copy_dumping(history, level) -> None:
    kind = {
        "test_delta": "CONFIDENCE_REVISED", "test_draft": "CRITIQUE_CREATED",
    }.get(level, "CLAIM_CREATED")
    event = find_event(history, kind)
    extra = {"unexpected": "field"}
    if level == "event":
        changed = event.model_copy(update=extra)
    else:
        payload = event.payload
        if level == "payload":
            payload = payload.model_copy(update=extra)
        else:
            record = payload.record
            if level == "record":
                record = record.model_copy(update=extra)
            elif level == "test_delta":
                record = record.model_copy(update={
                    "test_deltas": (record.test_deltas[0].model_copy(update=extra),),
                })
            else:
                record = record.model_copy(update={
                    "proposed_tests": (record.proposed_tests[0].model_copy(update=extra),),
                })
            payload = payload.model_copy(update={"record": record})
        changed = event.model_copy(update={"payload": payload})
    with pytest.raises(ReplayError, match="invalid.*|Extra inputs"):
        replay(rechain((*history[:history.index(event)], changed)))


def test_replay_revalidates_nested_critique_and_retrieval_contracts(history) -> None:
    critique = find_event(history, "CRITIQUE_CREATED")
    record = critique.payload.record
    draft = record.proposed_tests[0].model_copy(update={"evidence_ids": (MISSING_ID, MISSING_ID)})
    with pytest.raises(ReplayError, match="invalid"):
        replay(replace_payload(history, critique, critique.payload.model_copy(update={
            "record": record.model_copy(update={"proposed_tests": (draft,)}),
        })))
    retrieval = find_event(history, "RETRIEVAL_COMPLETED")
    payload = retrieval.payload
    for change in (
        {"query": payload.query.model_copy(update={"end_at": NOW - timedelta(seconds=1)})},
        {"results": (payload.results[0].model_copy(update={"total_score": Decimal("1.1")}),)},
    ):
        with pytest.raises(ReplayError, match="invalid"):
            replay(replace_payload(history, retrieval, payload.model_copy(update=change)))


@pytest.mark.parametrize(("field", "value"), (
    ("strength", Strength.HIGH), ("expected_observation", "A replacement prediction."),
    ("evidence_ids", ()),
))
def test_created_test_must_match_a_critique_proposal(history, field, value) -> None:
    event = find_event(history, "TEST_CREATED")
    payload = event.payload.model_copy(update={
        "record": event.payload.record.model_copy(update={field: value}),
    })
    with pytest.raises(ReplayError, match="critique|proposed"):
        replay(replace_payload(history, event, payload))


def test_critique_proposal_cannot_create_duplicate_tests_with_distinct_ids(history) -> None:
    event = find_event(history, "TEST_CREATED")
    prefix = history[:history.index(event) + 1]
    repeated = event.payload.model_copy(update={
        "record": event.payload.record.model_copy(update={"id": UUID(int=30)}),
    })
    with pytest.raises(ReplayError, match="duplicate"):
        replay((*prefix, make_event(repeated, prefix[-1])))


@pytest.mark.parametrize("change", (
    {"status": HypothesisStatus.LEADING}, {"current_confidence": Decimal("0.5000")},
))
def test_created_hypothesis_cannot_bypass_initial_status_or_confidence(history, change) -> None:
    event = find_event(history, "HYPOTHESIS_CREATED")
    payload = event.payload.model_copy(update={"record": event.payload.record.model_copy(update=change)})
    with pytest.raises(ReplayError, match="invalid event payload"):
        replay(replace_payload(history, event, payload))


def test_status_change_cannot_forge_the_confidence_based_status(history) -> None:
    event = find_event(history, "HYPOTHESIS_STATUS_CHANGED")
    with pytest.raises(ReplayError, match="status|confidence"):
        replay(replace_payload(history, event, event.payload.model_copy(update={
            "after": HypothesisStatus.LEADING,
        })))


@pytest.mark.parametrize("tied", (False, True))
def test_status_assignment_respects_the_entire_competition(history, tied) -> None:
    original = find_event(history, "HYPOTHESIS_CREATED")
    prefix = list(history[:history.index(original)])
    for identifier, confidence in ((20, "0.6000" if tied else "0.7000"), (21, "0.6000")):
        record = original.payload.record.model_copy(update={
            "id": UUID(int=identifier), "initial_confidence": Decimal(confidence),
            "current_confidence": Decimal(confidence),
        })
        prefix.append(make_event(HypothesisCreatedPayload(
            record=record, supporting_claim_ids=original.payload.supporting_claim_ids,
        ), prefix[-1]))
        revision = revise_confidence(record, (), NOW).model_copy(update={"id": UUID(int=identifier + 10)})
        prefix.append(make_event(ConfidenceRevisedPayload(record=revision), prefix[-1]))
    prefix.append(make_event(HypothesisStatusChangedPayload(
        hypothesis_id=UUID(int=20), before=HypothesisStatus.ACTIVE, after=HypothesisStatus.LEADING,
    ), prefix[-1]))
    if tied:
        with pytest.raises(ReplayError, match="status|confidence"):
            replay(tuple(prefix))
    else:
        projection = replay(tuple(prefix))
        assert projection.hypotheses[UUID(int=20)].status is HypothesisStatus.LEADING
        assert projection.hypotheses[UUID(int=21)].status is HypothesisStatus.ACTIVE


def test_multiple_test_deltas_preserve_order_in_canonical_state_and_reject_reordering(history) -> None:
    failed = find_event(history, "TEST_RESOLVED", 1)
    succeeded = failed.payload.record.model_copy(update={
        "status": HypothesisTestStatus.SUCCEEDED, "outcome": Outcome.SURVIVED,
    })
    events = replace_payload(history, failed, ResolvedTestPayload(record=succeeded))
    proposed = find_event(history, "HYPOTHESIS_CREATED").payload.record
    revision = revise_confidence(
        proposed, (succeeded, find_event(history, "TEST_RESOLVED").payload.record), NOW,
    ).model_copy(update={"id": UUID(int=12)})
    revised = make_event(ConfidenceRevisedPayload(record=revision), events[-1])
    events = (*events, revised)
    projection = replay(events)
    assert tuple(delta.test_id for delta in revision.test_deltas) == (UUID(int=10), UUID(int=11))
    reversed_revision = revision.model_copy(update={"test_deltas": revision.test_deltas[::-1]})
    changed = projection.model_copy(update={"revisions": {revision.id: reversed_revision}})
    assert changed.canonical_state() != projection.canonical_state()
    with pytest.raises(ReplayError, match="invalid event payload"):
        replay(replace_payload(events, revised, revised.payload.model_copy(update={
            "record": reversed_revision,
        })))


def test_completion_rejects_rehashed_retrieval_tampering(history) -> None:
    retrieval = find_event(history, "RETRIEVAL_COMPLETED")
    results = retrieval.payload.results
    altered = retrieval.model_copy(update={"payload": retrieval.payload.model_copy(update={
        "results": (results[0].model_copy(update={"total_score": Decimal("0.6300")}), *results[1:]),
    })})
    index = history.index(retrieval)
    with pytest.raises(ReplayError, match="projection"):
        replay(rechain((*history[:index], altered, *history[index + 1:])))


def test_complete_stage_allows_only_the_completion_event(history) -> None:
    payload = find_event(history, "CLAIM_CREATED").payload
    changed = payload.model_copy(update={"record": payload.record.model_copy(update={"id": MISSING_ID})})
    with pytest.raises(ReplayError, match="COMPLETE|terminal"):
        replay((*history[:-1], make_event(changed, history[-2])))


def test_canonical_decimal_scale_normalization_preserves_precision_and_normalizes_signed_zero(history) -> None:
    projection = replay(history)
    payload = projection.retrievals[UUID(int=13)]
    precise = payload.model_copy(update={"results": (
        payload.results[0].model_copy(update={
            "fts_score": Decimal("0.12345678901234567890123456789"),
        }), *payload.results[1:],
    )})
    projection = projection.model_copy(update={
        "retrievals": projection.retrievals | {precise.query_id: precise},
    })
    canonical = projection.canonical_state()
    result = canonical["retrievals"][str(precise.query_id)]["results"][0]
    assert result["fts_score"] == "0.12345678901234567890123456789"
    rescaled = precise.model_copy(update={"results": (
        precise.results[0].model_copy(update={
            "fts_score": Decimal("0.1234567890123456789012345678900"),
            "type_score": Decimal("-0.0000"),
        }), *precise.results[1:],
    )})
    assert projection.model_copy(update={
        "retrievals": projection.retrievals | {rescaled.query_id: rescaled},
    }).canonical_state() == canonical


def test_replay_does_not_read_entropy_or_time_functions(history, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("replay must not read entropy or time")

    with monkeypatch.context() as patch:
        patch.setattr("os.urandom", forbidden)
        patch.setattr("time.time", forbidden)
        patch.setattr("time.monotonic", forbidden)
        projection = replay(history)
    assert projection.status is InvestigationStatus.SUCCEEDED
