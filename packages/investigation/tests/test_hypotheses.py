from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from casezero_investigation import (
    ConfidenceRevision,
    ExecutionKind,
    HypothesisCritique,
    HypothesisTest,
    HypothesisTestDraft,
    HypothesisTestStatus,
)
from casezero_investigation import (
    TestDelta as DeltaRecord,
)
from casezero_investigation import (
    TestOutcome as Outcome,
)
from casezero_investigation import (
    TestStrength as Strength,
)
from casezero_investigation import (
    TestType as FalsificationType,
)
from pydantic import ValidationError

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def test_critique_forbids_support_field() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        HypothesisCritique.model_validate(
            {
                "investigation_id": uuid4(),
                "case_id": uuid4(),
                "hypothesis_id": uuid4(),
                "missing_evidence": (),
                "critique_confidence": Decimal("0.7000"),
                "proposed_tests": (),
                "supporting_evidence": (uuid4(),),
                "model_run_id": uuid4(),
                "created_at": NOW,
            }
        )


def test_semantic_test_requires_ai_and_deterministic_test_forbids_model() -> None:
    values = {
        "type": FalsificationType.SEMANTIC_COMPARISON,
        "expected_observation": "The descriptions conflict.",
        "strength": Strength.MEDIUM,
        "execution_kind": ExecutionKind.AI,
        "parameters": {"schema_version": "semantic-comparison-v1"},
        "evidence_ids": (uuid4(),),
        "claim_ids": (),
    }
    assert HypothesisTestDraft.model_validate(values).execution_kind is ExecutionKind.AI
    with pytest.raises(ValidationError, match="SEMANTIC_COMPARISON requires AI"):
        HypothesisTestDraft.model_validate(
            values | {"execution_kind": ExecutionKind.DETERMINISTIC}
        )
    with pytest.raises(ValidationError, match="deterministic test cannot use AI"):
        HypothesisTestDraft.model_validate(
            values
            | {
                "type": FalsificationType.EVIDENCE_PRESENCE,
                "execution_kind": ExecutionKind.AI,
            }
        )


def persisted_test_values() -> dict[str, object]:
    return {
        "investigation_id": uuid4(),
        "case_id": uuid4(),
        "hypothesis_id": uuid4(),
        "critique_id": uuid4(),
        "job_id": uuid4(),
        "type": FalsificationType.EVIDENCE_PRESENCE,
        "expected_observation": "A fuel-system anomaly should exist.",
        "strength": Strength.HIGH,
        "execution_kind": ExecutionKind.DETERMINISTIC,
        "parameters": {"schema_version": "evidence-presence-v1"},
        "status": HypothesisTestStatus.SUCCEEDED,
        "outcome": Outcome.EXPECTED_EVIDENCE_MISSING,
        "evidence_ids": (),
        "claim_ids": (),
        "model_run_id": None,
        "created_at": NOW,
        "completed_at": NOW,
    }


def test_persisted_test_requires_terminal_result_context() -> None:
    values = persisted_test_values()
    assert HypothesisTest.model_validate(values).outcome is Outcome.EXPECTED_EVIDENCE_MISSING
    with pytest.raises(ValidationError, match="completed_at and outcome"):
        HypothesisTest.model_validate(
            values | {"outcome": None, "completed_at": None}
        )


def test_confidence_revision_requires_ordered_test_deltas_and_exact_sum() -> None:
    first = uuid4()
    second = uuid4()
    ordered = tuple(sorted((first, second), key=str))
    deltas = (
        DeltaRecord(test_id=ordered[0], delta=Decimal("-0.1800")),
        DeltaRecord(test_id=ordered[1], delta=Decimal("0.0400")),
    )
    revision = ConfidenceRevision(
        investigation_id=uuid4(),
        case_id=uuid4(),
        hypothesis_id=uuid4(),
        before=Decimal("0.5000"),
        delta=Decimal("-0.1400"),
        after=Decimal("0.3600"),
        rule_version="weighted-delta-v1",
        test_deltas=deltas,
        rationale="Ordered deterministic test outcomes changed confidence.",
        created_at=NOW,
    )
    assert revision.after == Decimal("0.3600")
    with pytest.raises(ValidationError, match="sum"):
        ConfidenceRevision.model_validate(
            revision.model_dump() | {"delta": Decimal("-0.1300")}
        )
    with pytest.raises(ValidationError, match="ordered"):
        ConfidenceRevision.model_validate(revision.model_dump() | {"test_deltas": deltas[::-1]})


def test_pending_ai_test_does_not_require_a_model_run_before_execution() -> None:
    values = persisted_test_values() | {
        "type": FalsificationType.SEMANTIC_COMPARISON,
        "execution_kind": ExecutionKind.AI,
        "parameters": {"schema_version": "semantic-comparison-v1"},
        "status": HypothesisTestStatus.PENDING,
        "outcome": None,
        "completed_at": None,
    }
    pending = HypothesisTest.model_validate(values)
    assert pending.model_run_id is None
    assert HypothesisTest.model_validate_json(pending.model_dump_json()) == pending
    with pytest.raises(ValidationError, match="AI test requires model_run_id"):
        HypothesisTest.model_validate(
            values
            | {
                "status": HypothesisTestStatus.SUCCEEDED,
                "outcome": Outcome.INCONCLUSIVE,
                "completed_at": NOW,
            }
        )


@pytest.mark.parametrize("outcome", list(Outcome))
def test_failed_test_cannot_carry_a_successful_outcome(outcome: Outcome) -> None:
    values = persisted_test_values() | {"status": HypothesisTestStatus.FAILED}
    assert HypothesisTest.model_validate(values | {"outcome": None}).outcome is None
    with pytest.raises(ValidationError, match="failed test cannot have outcome"):
        HypothesisTest.model_validate(values | {"outcome": outcome})


def test_test_completion_cannot_precede_creation() -> None:
    with pytest.raises(ValidationError, match="precede"):
        HypothesisTest.model_validate(
            persisted_test_values() | {"completed_at": NOW - timedelta(seconds=1)}
        )


def test_revision_rejects_duplicate_test_deltas() -> None:
    delta = DeltaRecord(test_id=uuid4(), delta=Decimal("0.0400"))
    with pytest.raises(ValidationError, match="duplicate test"):
        ConfidenceRevision(
            investigation_id=uuid4(),
            case_id=uuid4(),
            hypothesis_id=uuid4(),
            before=Decimal("0.5000"),
            delta=Decimal("0.0800"),
            after=Decimal("0.5800"),
            rule_version="weighted-delta-v1",
            test_deltas=(delta, delta),
            rationale="Duplicate test IDs must not be counted twice.",
            created_at=NOW,
        )


@pytest.mark.parametrize(
    ("delta", "count", "after"),
    [("-0.3500", 4, "0.0500"), ("0.0600", 20, "0.9500")],
)
def test_revision_accepts_unlimited_summed_delta_before_clamping(
    delta: str, count: int, after: str
) -> None:
    test_deltas = tuple(
        DeltaRecord(test_id=UUID(int=index + 1), delta=Decimal(delta)) for index in range(count)
    )
    revision = ConfidenceRevision(
        investigation_id=uuid4(),
        case_id=uuid4(),
        hypothesis_id=uuid4(),
        before=Decimal("0.5000"),
        delta=sum((item.delta for item in test_deltas), Decimal("0.0000")),
        after=Decimal(after),
        rule_version="weighted-delta-v1",
        test_deltas=test_deltas,
        rationale="All successful deltas are summed before the confidence clamp.",
        created_at=NOW,
    )
    assert revision.after == Decimal(after)
    assert abs(revision.delta) > 1
