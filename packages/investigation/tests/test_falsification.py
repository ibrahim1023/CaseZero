from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from casezero_evidence.models import (
    EvidenceItem,
    EvidenceType,
    ExtractionMethod,
    TextLocator,
)
from casezero_investigation.falsification import execute_test
from casezero_investigation.hypotheses import (
    ClaimContradictionParameters,
    EvidencePresenceParameters,
    ExecutionKind,
    HypothesisTest,
    HypothesisTestDraft,
    HypothesisTestStatus,
    SemanticComparisonParameters,
    TemporalConsistencyParameters,
)
from casezero_investigation.hypotheses import TestOutcome as Outcome
from casezero_investigation.hypotheses import TestStrength as Strength
from casezero_investigation.hypotheses import TestType as FalsificationType
from casezero_investigation.models import Claim, ClaimStatus, TimelineEvent, TimePrecision
from pydantic import JsonValue, ValidationError

NOW = datetime(2026, 9, 5, tzinfo=UTC)
CASE_ID = UUID(int=100)
INVESTIGATION_ID = UUID(int=101)
PARAMETERS: tuple[tuple[FalsificationType, dict[str, JsonValue]], ...] = (
    (FalsificationType.EVIDENCE_PRESENCE, {"schema_version": "evidence-presence-v1"}),
    (
        FalsificationType.TEMPORAL_CONSISTENCY,
        {
            "schema_version": "temporal-consistency-v1",
            "before_event_id": str(UUID(int=10)),
            "after_event_id": str(UUID(int=11)),
        },
    ),
    (FalsificationType.CLAIM_CONTRADICTION, {"schema_version": "claim-contradiction-v1"}),
    (FalsificationType.SEMANTIC_COMPARISON, {"schema_version": "semantic-comparison-v1"}),
)


def pending_test(
    test_type: FalsificationType = FalsificationType.EVIDENCE_PRESENCE,
    parameters: dict[str, JsonValue] | None = None,
    evidence_ids: tuple[UUID, ...] = (),
    claim_ids: tuple[UUID, ...] = (),
) -> HypothesisTest:
    return HypothesisTest(
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        hypothesis_id=uuid4(),
        critique_id=uuid4(),
        job_id=uuid4(),
        type=test_type,
        expected_observation="An interruption record is expected under the hypothesis.",
        strength=Strength.HIGH,
        execution_kind=(
            ExecutionKind.AI
            if test_type is FalsificationType.SEMANTIC_COMPARISON
            else ExecutionKind.DETERMINISTIC
        ),
        parameters=dict(PARAMETERS)[test_type] if parameters is None else parameters,
        evidence_ids=evidence_ids,
        claim_ids=claim_ids,
        status=HypothesisTestStatus.PENDING,
        created_at=NOW,
    )


def evidence_item(
    identifier: int = 1,
    evidence_type: EvidenceType = EvidenceType.TEXT,
    subtype: str | None = None,
    observation: str = "Synthetic measurement record.",
) -> EvidenceItem:
    return EvidenceItem(
        id=UUID(int=identifier),
        case_id=CASE_ID,
        source_document_id=UUID(int=102),
        type=evidence_type,
        subtype=subtype,
        observation=observation,
        source_locator=TextLocator(start=0, end=len(observation)),
        extraction_method=ExtractionMethod.DETERMINISTIC,
    )


def claim(
    identifier: int = 20,
    supporting: tuple[int, ...] = (1,),
    contradicting: tuple[int, ...] = (),
    text: str = "An interruption was recorded.",
) -> Claim:
    return Claim(
        id=UUID(int=identifier),
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        text=text,
        status=ClaimStatus.OBSERVED,
        confidence=Decimal("0.7000"),
        source_candidate_ids=(uuid4(),),
        supporting_evidence_ids=tuple(UUID(int=value) for value in supporting),
        contradicting_evidence_ids=tuple(UUID(int=value) for value in contradicting),
        model_run_id=uuid4(),
        created_at=NOW,
    )


def timeline_event(
    identifier: int,
    occurred_at: datetime | None,
    precision: TimePrecision = TimePrecision.EXACT,
    evidence_id: int = 1,
) -> TimelineEvent:
    return TimelineEvent(
        id=UUID(int=identifier),
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        occurred_at=occurred_at,
        time_precision=precision,
        description="Synthetic event; wording does not determine temporal order.",
        confidence=Decimal("0.7000"),
        source_candidate_ids=(uuid4(),),
        evidence_ids=(UUID(int=evidence_id),),
        model_run_id=uuid4(),
        created_at=NOW,
    )


@pytest.mark.parametrize(("test_type", "parameters"), PARAMETERS)
def test_closed_parameters_survive_the_existing_json_dictionary_boundary(
    test_type: FalsificationType, parameters: dict[str, JsonValue]
) -> None:
    test = pending_test(test_type, parameters)
    expected_type = {
        FalsificationType.EVIDENCE_PRESENCE: EvidencePresenceParameters,
        FalsificationType.TEMPORAL_CONSISTENCY: TemporalConsistencyParameters,
        FalsificationType.CLAIM_CONTRADICTION: ClaimContradictionParameters,
        FalsificationType.SEMANTIC_COMPARISON: SemanticComparisonParameters,
    }[test_type]
    assert isinstance(test.typed_parameters, expected_type)
    assert isinstance(test.parameters, dict)
    assert HypothesisTest.model_validate_json(test.model_dump_json()) == test


@pytest.mark.parametrize(("test_type", "parameters"), PARAMETERS)
@pytest.mark.parametrize("field", ["sql", "outcome", "is_true"])
def test_parameters_reject_queries_and_predeclared_truth(
    test_type: FalsificationType, parameters: dict[str, JsonValue], field: str
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        pending_test(test_type, parameters | {field: "untrusted"})


@pytest.mark.parametrize(("test_type", "parameters"), PARAMETERS)
@pytest.mark.parametrize("version", [None, 1, "unknown-v1"])
def test_parameters_reject_wrong_schema_versions(
    test_type: FalsificationType, parameters: dict[str, JsonValue], version: JsonValue
) -> None:
    with pytest.raises(ValidationError):
        pending_test(test_type, parameters | {"schema_version": version})


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"schema_version": "evidence-presence-v1", "evidence_type": "invented"},
        {"schema_version": "evidence-presence-v1", "subtype": ""},
        {"schema_version": "evidence-presence-v1", "subtype": "   "},
        {"schema_version": "evidence-presence-v1", "subtype": 3},
    ],
)
def test_presence_selectors_are_strict(parameters: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        pending_test(parameters=parameters)


def test_temporal_parameters_require_two_distinct_well_formed_event_ids() -> None:
    parameters = dict(PARAMETERS)[FalsificationType.TEMPORAL_CONSISTENCY]
    for invalid in ("not-a-uuid", None, str(UUID(int=10))):
        with pytest.raises(ValidationError):
            pending_test(
                FalsificationType.TEMPORAL_CONSISTENCY,
                parameters | {"after_event_id": invalid},
            )
    with pytest.raises(ValidationError, match="Field required"):
        pending_test(
            FalsificationType.TEMPORAL_CONSISTENCY,
            {"schema_version": "temporal-consistency-v1"},
        )


@pytest.mark.parametrize("field", ["evidence_ids", "claim_ids"])
def test_draft_rejects_duplicate_reference_ids(field: str) -> None:
    values = pending_test().model_dump(include=set(HypothesisTestDraft.model_fields))
    with pytest.raises(ValidationError, match="unique"):
        HypothesisTestDraft.model_validate(values | {field: (UUID(int=1), UUID(int=1))})


def test_presence_matches_explicit_metadata_and_records_ordered_evidence() -> None:
    test = pending_test(
        parameters={
            "schema_version": "evidence-presence-v1",
            "evidence_type": "TIME_SERIES",
            "subtype": "signal",
        }
    )
    evidence = (
        evidence_item(5, EvidenceType.TIME_SERIES, "signal"),
        evidence_item(1, EvidenceType.TEXT, "signal"),
        evidence_item(2, EvidenceType.TIME_SERIES, "signal"),
    )
    completed_at = NOW + timedelta(seconds=1)
    result = execute_test(test, evidence, (), (), completed_at)
    assert result == execute_test(test, evidence[::-1], (), (), completed_at)
    assert result.status is HypothesisTestStatus.SUCCEEDED
    assert result.outcome is Outcome.SURVIVED
    assert result.result_evidence_ids == (UUID(int=2), UUID(int=5))
    assert result.claim_ids == ()
    assert result.completed_at == completed_at
    assert result.model_run_id is None
    assert result.id == test.id
    assert result.hypothesis_id == test.hypothesis_id
    assert result.critique_id == test.critique_id
    assert result.job_id == test.job_id
    assert test.status is HypothesisTestStatus.PENDING
    assert test.outcome is None
    assert test.completed_at is None
    assert test.evidence_ids == ()


def test_presence_can_check_named_existing_evidence_without_a_metadata_selector() -> None:
    item = evidence_item()
    test = pending_test(evidence_ids=(item.id,))
    result = execute_test(test, (item,), (), (), NOW)
    assert result.outcome is Outcome.SURVIVED
    assert result.result_evidence_ids == (item.id,)


@pytest.mark.parametrize("empty", [True, False])
def test_presence_returns_missing_only_for_an_explicit_selector(empty: bool) -> None:
    test = pending_test(
        parameters={"schema_version": "evidence-presence-v1", "evidence_type": "TIME_SERIES"}
    )
    evidence = () if empty else (evidence_item(observation="TIME_SERIES interruption record"),)
    assert execute_test(test, evidence, (), (), NOW).outcome is Outcome.EXPECTED_EVIDENCE_MISSING


def test_presence_subtype_selection_is_literal_metadata_not_semantic_matching() -> None:
    test = pending_test(parameters={"schema_version": "evidence-presence-v1", "subtype": "signal"})
    result = execute_test(test, (evidence_item(observation="signal detected"),), (), (), NOW)
    assert result.outcome is Outcome.EXPECTED_EVIDENCE_MISSING


@pytest.mark.parametrize("empty", [True, False])
def test_presence_without_ids_or_selectors_does_not_turn_prose_into_an_expectation(empty: bool) -> None:
    test = pending_test()
    evidence = () if empty else (evidence_item(),)
    result = execute_test(test, evidence, (), (), NOW)
    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.result_evidence_ids == ()


def test_presence_does_not_search_outside_explicit_input_ids() -> None:
    selected = evidence_item(1)
    test = pending_test(
        parameters={"schema_version": "evidence-presence-v1", "evidence_type": "TIME_SERIES"},
        evidence_ids=(selected.id,),
    )
    result = execute_test(test, (selected, evidence_item(2, EvidenceType.TIME_SERIES)), (), (), NOW)
    assert result.outcome is Outcome.EXPECTED_EVIDENCE_MISSING
    assert result.evidence_ids == (selected.id,)
    assert result.result_evidence_ids == ()


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(1, Outcome.SURVIVED), (0, Outcome.CONTRADICTED), (-1, Outcome.CONTRADICTED)],
)
def test_temporal_consistency_compares_exact_timestamps(seconds: int, expected: Outcome) -> None:
    test = pending_test(FalsificationType.TEMPORAL_CONSISTENCY)
    events = (
        timeline_event(11, NOW + timedelta(seconds=seconds), evidence_id=2),
        timeline_event(10, NOW),
    )
    evidence = (evidence_item(2), evidence_item(1))
    result = execute_test(test, evidence, (), events, NOW)
    assert result == execute_test(test, evidence[::-1], (), events[::-1], NOW)
    assert result.outcome is expected
    assert result.result_evidence_ids == (UUID(int=1), UUID(int=2))


@pytest.mark.parametrize("seconds", (-1, 1))
@pytest.mark.parametrize(("before_evidence_id", "used_ids"), ((1, (UUID(int=1),)), (2, ())))
def test_temporal_consistency_respects_explicit_evidence_scope(
    seconds: int, before_evidence_id: int, used_ids: tuple[UUID, ...],
) -> None:
    selected = evidence_item(1)
    test = pending_test(FalsificationType.TEMPORAL_CONSISTENCY, evidence_ids=(selected.id,))
    events = (
        timeline_event(10, NOW, evidence_id=before_evidence_id),
        timeline_event(11, NOW + timedelta(seconds=seconds), evidence_id=2),
    )
    result = execute_test(test, (selected, evidence_item(2)), (), events, NOW)
    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.evidence_ids == (selected.id,)
    assert result.result_evidence_ids == used_ids


@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize("precision", list(TimePrecision))
def test_temporal_consistency_with_missing_timestamps_is_inconclusive(
    position: int, precision: TimePrecision
) -> None:
    events = [timeline_event(10, NOW), timeline_event(11, NOW + timedelta(seconds=1))]
    events[position] = timeline_event(10 + position, None, precision)
    result = execute_test(
        pending_test(FalsificationType.TEMPORAL_CONSISTENCY),
        (evidence_item(),), (), tuple(events), NOW,
    )
    assert result.outcome is Outcome.INCONCLUSIVE


@pytest.mark.parametrize(
    "precision", [TimePrecision.APPROXIMATE, TimePrecision.RELATIVE, TimePrecision.UNKNOWN]
)
def test_temporal_consistency_does_not_treat_uncertain_times_as_exact(precision: TimePrecision) -> None:
    events = (timeline_event(10, NOW, precision), timeline_event(11, NOW + timedelta(seconds=1)))
    result = execute_test(
        pending_test(FalsificationType.TEMPORAL_CONSISTENCY), (evidence_item(),), (), events, NOW
    )
    assert result.outcome is Outcome.INCONCLUSIVE


def test_temporal_consistency_without_active_evidence_is_inconclusive() -> None:
    events = (timeline_event(10, NOW), timeline_event(11, NOW + timedelta(seconds=1)))
    result = execute_test(pending_test(FalsificationType.TEMPORAL_CONSISTENCY), (), (), events, NOW)
    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.result_evidence_ids == ()


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ((1, 2), Outcome.CONTRADICTED),
        ((1,), Outcome.SURVIVED),
        ((2,), Outcome.CONTRADICTED),
        ((3,), Outcome.INCONCLUSIVE),
        ((), Outcome.CONTRADICTED),
    ],
)
def test_claim_contradiction_uses_explicit_polarities_of_available_evidence(
    selected: tuple[int, ...], expected: Outcome
) -> None:
    target = claim(contradicting=(2,))
    test = pending_test(
        FalsificationType.CLAIM_CONTRADICTION,
        evidence_ids=tuple(UUID(int=value) for value in selected),
        claim_ids=(target.id,),
    )
    result = execute_test(test, (evidence_item(3), evidence_item(2), evidence_item(1)), (target,), (), NOW)
    assert result.outcome is expected
    assert result.claim_ids == (target.id,)
    if not selected:
        assert result.result_evidence_ids == (UUID(int=1), UUID(int=2))


def test_claim_contradiction_does_not_invent_semantic_conflicts_from_opposite_words() -> None:
    claims = (claim(20, text="An interruption occurred."), claim(21, text="No interruption occurred."))
    test = pending_test(FalsificationType.CLAIM_CONTRADICTION, claim_ids=(claims[1].id, claims[0].id))
    result = execute_test(test, (evidence_item(observation="contradictory record"),), claims, (), NOW)
    assert result.outcome is Outcome.SURVIVED
    assert result.claim_ids == (UUID(int=20), UUID(int=21))


def test_claim_contradiction_without_active_linked_evidence_is_inconclusive() -> None:
    target = claim(supporting=(), contradicting=(2,))
    test = pending_test(FalsificationType.CLAIM_CONTRADICTION, claim_ids=(target.id,))
    result = execute_test(test, (evidence_item(),), (target,), (), NOW)
    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.result_evidence_ids == ()


def test_claim_contradiction_needs_support_for_every_selected_claim_to_survive() -> None:
    claims = (claim(20), claim(21, supporting=(2,)))
    test = pending_test(FalsificationType.CLAIM_CONTRADICTION, claim_ids=tuple(item.id for item in claims))
    result = execute_test(test, (evidence_item(),), claims, (), NOW)
    assert result.outcome is Outcome.INCONCLUSIVE


def test_claim_contradiction_without_selected_claims_is_inconclusive() -> None:
    result = execute_test(
        pending_test(FalsificationType.CLAIM_CONTRADICTION), (evidence_item(),), (claim(),), (), NOW
    )
    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.claim_ids == result.result_evidence_ids == ()


def test_ambiguous_polarity_for_the_same_evidence_is_inconclusive() -> None:
    target = claim(contradicting=(1,))
    test = pending_test(FalsificationType.CLAIM_CONTRADICTION, claim_ids=(target.id,))
    assert execute_test(test, (evidence_item(),), (target,), (), NOW).outcome is Outcome.INCONCLUSIVE


def test_semantic_comparison_requires_a_separate_ai_caller_without_fabricating_a_result() -> None:
    test = pending_test(FalsificationType.SEMANTIC_COMPARISON)
    assert test.model_run_id is None
    with pytest.raises(ValueError, match="separate AI"):
        execute_test(test, (), (), (), NOW)
    assert test.status is HypothesisTestStatus.PENDING
    assert test.outcome is None
    assert test.completed_at is None
    assert test.model_run_id is None


@pytest.mark.parametrize("field", ["evidence_ids", "claim_ids"])
def test_executor_rejects_unknown_top_level_ids(field: str) -> None:
    test = HypothesisTest.model_validate(pending_test().model_dump() | {field: (UUID(int=999),)})
    with pytest.raises(ValueError, match="unknown"):
        execute_test(test, (evidence_item(),), (claim(),), (), NOW)


@pytest.mark.parametrize("field", ["before_event_id", "after_event_id"])
def test_executor_rejects_unknown_temporal_parameter_ids(field: str) -> None:
    parameters = dict(PARAMETERS)[FalsificationType.TEMPORAL_CONSISTENCY] | {field: str(UUID(int=999))}
    test = pending_test(FalsificationType.TEMPORAL_CONSISTENCY, parameters)
    events = (timeline_event(10, NOW), timeline_event(11, NOW + timedelta(seconds=1)))
    with pytest.raises(ValueError, match="unknown.*event"):
        execute_test(test, (evidence_item(),), (), events, NOW)


def test_executor_revalidates_mutated_parameters_at_execution_boundary() -> None:
    test = pending_test()
    test.parameters["sql"] = "select true"
    with pytest.raises(ValidationError, match="Extra inputs"):
        execute_test(test, (), (), (), NOW)
    assert test.status is HypothesisTestStatus.PENDING


@pytest.mark.parametrize("kind", ["evidence", "claim", "timeline"])
def test_executor_rejects_duplicate_state_ids(kind: str) -> None:
    item = evidence_item()
    target = claim()
    event = timeline_event(10, NOW)
    evidence = (item, item) if kind == "evidence" else (item,)
    claims = (target, target) if kind == "claim" else (target,)
    timeline = (event, event) if kind == "timeline" else (event,)
    with pytest.raises(ValueError, match="duplicate"):
        execute_test(pending_test(), evidence, claims, timeline, NOW)


@pytest.mark.parametrize("kind", ["evidence", "claim", "timeline"])
def test_executor_rejects_cross_case_state_even_if_not_selected(kind: str) -> None:
    item = evidence_item()
    target = claim()
    event = timeline_event(10, NOW)
    if kind == "evidence":
        item = EvidenceItem.model_validate(item.model_dump() | {"case_id": uuid4()})
    elif kind == "claim":
        target = Claim.model_validate(target.model_dump() | {"case_id": uuid4()})
    else:
        event = TimelineEvent.model_validate(event.model_dump() | {"case_id": uuid4()})
    with pytest.raises(ValueError, match="context"):
        execute_test(pending_test(), (item,), (target,), (event,), NOW)


@pytest.mark.parametrize("kind", ["claim", "timeline"])
def test_executor_rejects_cross_investigation_canonical_state(kind: str) -> None:
    target = claim()
    event = timeline_event(10, NOW)
    if kind == "claim":
        target = Claim.model_validate(target.model_dump() | {"investigation_id": uuid4()})
    else:
        event = TimelineEvent.model_validate(event.model_dump() | {"investigation_id": uuid4()})
    with pytest.raises(ValueError, match="context"):
        execute_test(pending_test(), (evidence_item(),), (target,), (event,), NOW)


@pytest.mark.parametrize("status", [HypothesisTestStatus.SUCCEEDED, HypothesisTestStatus.FAILED])
def test_executor_does_not_reexecute_terminal_tests(status: HypothesisTestStatus) -> None:
    test = HypothesisTest.model_validate(
        pending_test().model_dump()
        | {
            "status": status,
            "outcome": Outcome.INCONCLUSIVE if status is HypothesisTestStatus.SUCCEEDED else None,
            "completed_at": NOW,
        }
    )
    with pytest.raises(ValueError, match="PENDING"):
        execute_test(test, (), (), (), NOW)


@pytest.mark.parametrize("completed_at", [NOW.replace(tzinfo=None), NOW - timedelta(seconds=1)])
def test_executor_rejects_invalid_completion_time(completed_at: datetime) -> None:
    with pytest.raises(ValueError, match="UTC-aware|precede"):
        execute_test(pending_test(), (), (), (), completed_at)
