from datetime import datetime
from decimal import Decimal

from casezero_investigation.hypotheses import (
    ConfidenceRevision,
    HypothesisTest,
    HypothesisTestStatus,
    TestDelta,
    TestOutcome,
    TestStrength,
)
from casezero_investigation.models import Hypothesis, HypothesisStatus, _require_four_places

_WEIGHTS: dict[TestOutcome, dict[TestStrength, Decimal]] = {
    TestOutcome.CONTRADICTED: {
        TestStrength.LOW: Decimal("-0.0800"),
        TestStrength.MEDIUM: Decimal("-0.1800"),
        TestStrength.HIGH: Decimal("-0.3500"),
    },
    TestOutcome.EXPECTED_EVIDENCE_MISSING: {
        TestStrength.LOW: Decimal("-0.0300"),
        TestStrength.MEDIUM: Decimal("-0.0800"),
        TestStrength.HIGH: Decimal("-0.1500"),
    },
    TestOutcome.SURVIVED: {
        TestStrength.LOW: Decimal("0.0200"),
        TestStrength.MEDIUM: Decimal("0.0400"),
        TestStrength.HIGH: Decimal("0.0600"),
    },
    TestOutcome.INCONCLUSIVE: {
        TestStrength.LOW: Decimal("0.0000"),
        TestStrength.MEDIUM: Decimal("0.0000"),
        TestStrength.HIGH: Decimal("0.0000"),
    },
}


def test_delta(outcome: TestOutcome, strength: TestStrength) -> Decimal:
    if not isinstance(outcome, TestOutcome) or not isinstance(strength, TestStrength):
        raise TypeError("test delta requires a TestOutcome and TestStrength")
    return _WEIGHTS[outcome][strength]


def revise_confidence(
    hypothesis: Hypothesis, tests: tuple[HypothesisTest, ...], created_at: datetime
) -> ConfidenceRevision:
    hypothesis = Hypothesis.model_validate(hypothesis.model_dump())
    if len({test.id for test in tests}) != len(tests):
        raise ValueError("duplicate test IDs in confidence revision")
    deltas: list[TestDelta] = []
    reasons: list[str] = []
    for item in sorted(tests, key=lambda test: str(test.id)):
        test = HypothesisTest.model_validate(item.model_dump())
        if (
            test.case_id != hypothesis.case_id
            or test.investigation_id != hypothesis.investigation_id
            or test.hypothesis_id != hypothesis.id
        ):
            raise ValueError("test does not match hypothesis context")
        if test.status is not HypothesisTestStatus.SUCCEEDED:
            continue
        if test.outcome is None:
            raise ValueError("successful test requires an outcome")
        delta = test_delta(test.outcome, test.strength)
        deltas.append(TestDelta(test_id=test.id, delta=delta))
        reasons.append(f"{test.id}:{test.outcome.value}:{test.strength.value}:{delta:+.4f}")
    summed_delta = _require_four_places(sum((item.delta for item in deltas), Decimal("0.0000")))
    before = hypothesis.current_confidence
    after = _require_four_places(min(Decimal("0.9500"), max(Decimal("0.0500"), before + summed_delta)))
    rationale = "; ".join(reasons) if reasons else "no SUCCEEDED tests"
    return ConfidenceRevision(
        investigation_id=hypothesis.investigation_id,
        case_id=hypothesis.case_id,
        hypothesis_id=hypothesis.id,
        before=before,
        delta=summed_delta,
        after=after,
        rule_version="weighted-delta-v1",
        test_deltas=tuple(deltas),
        rationale=(
            f"weighted-delta-v1; {rationale}; before={before:.4f}; "
            f"delta={summed_delta:+.4f}; after={after:.4f}; clamp=[0.0500,0.9500]"
        ),
        created_at=created_at,
    )


def assign_hypothesis_statuses(hypotheses: tuple[Hypothesis, ...]) -> tuple[Hypothesis, ...]:
    if not hypotheses:
        return ()
    if len({hypothesis.id for hypothesis in hypotheses}) != len(hypotheses):
        raise ValueError("duplicate hypothesis IDs in status assignment")
    hypotheses = tuple(Hypothesis.model_validate(item.model_dump()) for item in hypotheses)
    context = hypotheses[0]
    if any(
        item.case_id != context.case_id or item.investigation_id != context.investigation_id
        for item in hypotheses
    ):
        raise ValueError("hypotheses must share investigation context")
    highest = max(item.current_confidence for item in hypotheses)
    leaders = tuple(item.id for item in hypotheses if item.current_confidence == highest)
    leading_id = leaders[0] if highest >= Decimal("0.5000") and len(leaders) == 1 else None
    assigned: list[Hypothesis] = []
    for hypothesis in hypotheses:
        if hypothesis.current_confidence <= Decimal("0.1500"):
            status = HypothesisStatus.REJECTED
        elif hypothesis.current_confidence < Decimal("0.3500"):
            status = HypothesisStatus.WEAKENED
        elif hypothesis.id == leading_id:
            status = HypothesisStatus.LEADING
        else:
            status = HypothesisStatus.ACTIVE
        assigned.append(hypothesis.model_copy(update={"status": status}))
    return tuple(assigned)
