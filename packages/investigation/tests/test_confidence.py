from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from casezero_investigation.confidence import (
    assign_hypothesis_statuses,
    revise_confidence,
)
from casezero_investigation.confidence import test_delta as delta_for
from casezero_investigation.hypotheses import (
    ExecutionKind,
    HypothesisTest,
    HypothesisTestStatus,
)
from casezero_investigation.hypotheses import TestOutcome as Outcome
from casezero_investigation.hypotheses import TestStrength as Strength
from casezero_investigation.hypotheses import TestType as FalsificationType
from casezero_investigation.models import Hypothesis, HypothesisStatus

NOW = datetime(2026, 9, 5, tzinfo=UTC)
CASE_ID = UUID(int=100)
INVESTIGATION_ID = UUID(int=101)


def hypothesis(
    confidence: str = "0.5000", status: HypothesisStatus = HypothesisStatus.ACTIVE
) -> Hypothesis:
    return Hypothesis(
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        title="Possible interruption",
        description="An interruption may explain the observations.",
        initial_confidence=Decimal("0.5000"),
        current_confidence=Decimal(confidence),
        status=status,
        distinguishing_prediction="A recorded interruption should exist.",
        weakening_evidence="An uninterrupted record would weaken this hypothesis.",
        model_run_id=uuid4(),
        created_at=NOW,
    )


def completed_test(
    target: Hypothesis,
    test_id: int,
    outcome: Outcome = Outcome.CONTRADICTED,
    strength: Strength = Strength.MEDIUM,
) -> HypothesisTest:
    return HypothesisTest(
        id=UUID(int=test_id),
        investigation_id=target.investigation_id,
        case_id=target.case_id,
        hypothesis_id=target.id,
        critique_id=uuid4(),
        job_id=uuid4(),
        type=FalsificationType.EVIDENCE_PRESENCE,
        expected_observation="An interruption record should exist.",
        strength=strength,
        execution_kind=ExecutionKind.DETERMINISTIC,
        parameters={"schema_version": "evidence-presence-v1"},
        evidence_ids=(),
        claim_ids=(),
        status=HypothesisTestStatus.SUCCEEDED,
        outcome=outcome,
        created_at=NOW,
        completed_at=NOW,
    )


@pytest.mark.parametrize(
    ("outcome", "strength", "expected"),
    [
        (Outcome.CONTRADICTED, Strength.LOW, "-0.0800"),
        (Outcome.CONTRADICTED, Strength.MEDIUM, "-0.1800"),
        (Outcome.CONTRADICTED, Strength.HIGH, "-0.3500"),
        (Outcome.EXPECTED_EVIDENCE_MISSING, Strength.LOW, "-0.0300"),
        (Outcome.EXPECTED_EVIDENCE_MISSING, Strength.MEDIUM, "-0.0800"),
        (Outcome.EXPECTED_EVIDENCE_MISSING, Strength.HIGH, "-0.1500"),
        (Outcome.SURVIVED, Strength.LOW, "0.0200"),
        (Outcome.SURVIVED, Strength.MEDIUM, "0.0400"),
        (Outcome.SURVIVED, Strength.HIGH, "0.0600"),
        (Outcome.INCONCLUSIVE, Strength.LOW, "0.0000"),
        (Outcome.INCONCLUSIVE, Strength.MEDIUM, "0.0000"),
        (Outcome.INCONCLUSIVE, Strength.HIGH, "0.0000"),
    ],
)
def test_weighted_delta_table(outcome: Outcome, strength: Strength, expected: str) -> None:
    delta = delta_for(outcome, strength)
    assert isinstance(delta, Decimal)
    assert str(delta) == expected


@pytest.mark.parametrize(
    ("outcome", "strength"),
    [("CONTRADICTED", Strength.HIGH), (Outcome.SURVIVED, "HIGH")],
)
def test_delta_requires_typed_codes(outcome: object, strength: object) -> None:
    with pytest.raises(TypeError, match="TestOutcome and TestStrength"):
        delta_for(cast(Outcome, outcome), cast(Strength, strength))


def test_revision_orders_outcomes_and_builds_stable_numeric_rationale() -> None:
    target = hypothesis("0.5432")
    tests = (
        completed_test(target, 3, Outcome.INCONCLUSIVE, Strength.HIGH),
        completed_test(target, 2, Outcome.SURVIVED, Strength.MEDIUM),
        completed_test(target, 1, Outcome.CONTRADICTED, Strength.MEDIUM),
    )
    created_at = NOW + timedelta(seconds=1)
    revision = revise_confidence(target, tests, created_at)
    reversed_revision = revise_confidence(target, tests[::-1], created_at)

    assert revision.model_dump(exclude={"id"}) == reversed_revision.model_dump(exclude={"id"})
    assert revision.investigation_id == target.investigation_id
    assert revision.case_id == target.case_id
    assert revision.hypothesis_id == target.id
    assert revision.created_at == created_at
    assert revision.rule_version == "weighted-delta-v1"
    assert str(revision.before) == "0.5432"
    assert str(revision.delta) == "-0.1400"
    assert str(revision.after) == "0.4032"
    assert tuple(item.test_id for item in revision.test_deltas) == (
        UUID(int=1), UUID(int=2), UUID(int=3)
    )
    assert tuple(item.delta for item in revision.test_deltas) == (
        Decimal("-0.1800"), Decimal("0.0400"), Decimal("0.0000")
    )
    assert revision.rationale.index(str(UUID(int=1))) < revision.rationale.index(str(UUID(int=2)))
    assert revision.rationale.index(str(UUID(int=2))) < revision.rationale.index(str(UUID(int=3)))
    for code in ("CONTRADICTED", "SURVIVED", "INCONCLUSIVE", "MEDIUM", "HIGH", "weighted-delta-v1"):
        assert code in revision.rationale
    for amount in ("-0.1800", "+0.0400", "+0.0000", "0.5432", "-0.1400", "0.4032"):
        assert amount in revision.rationale
    assert target.current_confidence == Decimal("0.5432")
    assert tuple(test.id.int for test in tests) == (3, 2, 1)


@pytest.mark.parametrize(
    ("before", "outcome", "count", "expected_delta", "after"),
    [
        ("0.5000", Outcome.CONTRADICTED, 4, "-1.4000", "0.0500"),
        ("0.5000", Outcome.SURVIVED, 20, "1.2000", "0.9500"),
        ("0.4000", Outcome.CONTRADICTED, 1, "-0.3500", "0.0500"),
        ("0.8900", Outcome.SURVIVED, 1, "0.0600", "0.9500"),
    ],
)
def test_revision_sums_without_a_delta_limit_then_clamps_once(
    before: str, outcome: Outcome, count: int, expected_delta: str, after: str
) -> None:
    target = hypothesis(before)
    tests = tuple(completed_test(target, index + 1, outcome, Strength.HIGH) for index in range(count))
    revision = revise_confidence(target, tests, NOW)
    assert str(revision.delta) == expected_delta
    assert str(revision.after) == after
    assert len(revision.test_deltas) == count


def test_revision_does_not_clamp_between_tests() -> None:
    target = hypothesis("0.1000")
    tests = (
        completed_test(target, 1, Outcome.CONTRADICTED, Strength.HIGH),
        completed_test(target, 2, Outcome.SURVIVED, Strength.HIGH),
    )
    revision = revise_confidence(target, tests, NOW)
    assert revision.delta == Decimal("-0.2900")
    assert revision.after == Decimal("0.0500")


def test_only_succeeded_tests_contribute_even_when_ai_executed_them() -> None:
    target = hypothesis()
    template = completed_test(target, 1)
    pending = HypothesisTest.model_validate(
        template.model_dump()
        | {"status": HypothesisTestStatus.PENDING, "outcome": None, "completed_at": None}
    )
    failed = HypothesisTest.model_validate(
        template.model_dump()
        | {"id": UUID(int=2), "status": HypothesisTestStatus.FAILED, "outcome": None}
    )
    succeeded_ai = HypothesisTest.model_validate(
        template.model_dump()
        | {
            "id": UUID(int=3),
            "type": FalsificationType.SEMANTIC_COMPARISON,
            "execution_kind": ExecutionKind.AI,
            "parameters": {"schema_version": "semantic-comparison-v1"},
            "model_run_id": uuid4(),
        }
    )
    revision = revise_confidence(target, (failed, pending, succeeded_ai), NOW)
    assert tuple(delta.test_id for delta in revision.test_deltas) == (succeeded_ai.id,)
    assert revision.delta == Decimal("-0.1800")
    assert str(pending.id) not in revision.rationale
    assert str(failed.id) not in revision.rationale


def test_empty_or_inconclusive_revision_never_increases_confidence() -> None:
    target = hypothesis("0.4321")
    empty = revise_confidence(target, (), NOW)
    inconclusive = revise_confidence(
        target, (completed_test(target, 1, Outcome.INCONCLUSIVE),), NOW
    )
    assert empty.before == empty.after == inconclusive.after == Decimal("0.4321")
    assert empty.delta == inconclusive.delta == Decimal("0.0000")
    assert empty.test_deltas == ()
    assert "no SUCCEEDED tests" in empty.rationale


@pytest.mark.parametrize("status", list(HypothesisTestStatus))
def test_revision_rejects_duplicate_test_ids_including_ignored_tests(
    status: HypothesisTestStatus,
) -> None:
    target = hypothesis()
    test = completed_test(target, 1)
    if status is not HypothesisTestStatus.SUCCEEDED:
        test = HypothesisTest.model_validate(
            test.model_dump()
            | {
                "status": status,
                "outcome": None,
                "completed_at": None if status is HypothesisTestStatus.PENDING else NOW,
            }
        )
    with pytest.raises(ValueError, match="duplicate test"):
        revise_confidence(target, (test, test), NOW)


@pytest.mark.parametrize("field", ["hypothesis_id", "case_id", "investigation_id"])
def test_revision_rejects_tests_from_another_context(field: str) -> None:
    target = hypothesis()
    foreign = HypothesisTest.model_validate(completed_test(target, 1).model_dump() | {field: uuid4()})
    with pytest.raises(ValueError, match="context"):
        revise_confidence(target, (foreign,), NOW)


def test_revision_rejects_non_utc_created_at() -> None:
    with pytest.raises(ValueError, match="UTC-aware"):
        revise_confidence(hypothesis(), (), NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        ("0.0000", HypothesisStatus.REJECTED),
        ("0.1500", HypothesisStatus.REJECTED),
        ("0.1501", HypothesisStatus.WEAKENED),
        ("0.3499", HypothesisStatus.WEAKENED),
        ("0.3500", HypothesisStatus.ACTIVE),
        ("0.4999", HypothesisStatus.ACTIVE),
        ("0.5000", HypothesisStatus.LEADING),
        ("1.0000", HypothesisStatus.LEADING),
    ],
)
def test_status_thresholds_reset_previous_statuses(confidence: str, expected: HypothesisStatus) -> None:
    target = hypothesis(confidence, HypothesisStatus.LEADING)
    updated, = assign_hypothesis_statuses((target,))
    assert updated.status is expected
    assert updated.model_dump(exclude={"status"}) == target.model_dump(exclude={"status"})
    assert target.status is HypothesisStatus.LEADING


def test_only_unique_highest_hypothesis_leads_and_input_order_is_preserved() -> None:
    targets = (hypothesis("0.6000"), hypothesis("0.8000"), hypothesis("0.5000"))
    result = assign_hypothesis_statuses(targets)
    assert tuple(item.id for item in result) == tuple(item.id for item in targets)
    assert tuple(item.status for item in result) == (
        HypothesisStatus.ACTIVE, HypothesisStatus.LEADING, HypothesisStatus.ACTIVE
    )


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        ("0.1500", HypothesisStatus.REJECTED),
        ("0.2000", HypothesisStatus.WEAKENED),
        ("0.5000", HypothesisStatus.ACTIVE),
        ("0.9000", HypothesisStatus.ACTIVE),
    ],
)
def test_tied_highest_never_leads_or_overrides_lower_thresholds(
    confidence: str, expected: HypothesisStatus
) -> None:
    targets = (hypothesis(confidence, HypothesisStatus.LEADING), hypothesis(confidence))
    assert tuple(item.status for item in assign_hypothesis_statuses(targets)) == (expected, expected)


def test_empty_competition_has_no_statuses() -> None:
    assert assign_hypothesis_statuses(()) == ()


def test_status_assignment_rejects_duplicate_hypothesis_ids() -> None:
    target = hypothesis()
    with pytest.raises(ValueError, match="duplicate hypothesis"):
        assign_hypothesis_statuses((target, target))


@pytest.mark.parametrize("field", ["case_id", "investigation_id"])
def test_status_assignment_does_not_compare_different_investigations(field: str) -> None:
    target = hypothesis()
    foreign = Hypothesis.model_validate(hypothesis().model_dump() | {field: uuid4()})
    with pytest.raises(ValueError, match="context"):
        assign_hypothesis_statuses((target, foreign))
