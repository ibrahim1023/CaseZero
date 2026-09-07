from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal, localcontext
from uuid import UUID, uuid4

import pytest
from casezero_evidence.models import EvidenceItem, EvidenceType, ExtractionMethod, TextLocator
from casezero_investigation.critic import (
    CRITIC_PROMPT,
    CritiqueBundle,
    CritiqueDraft,
    materialize_critique,
)
from casezero_investigation.hypotheses import (
    EvidencePresenceParameters,
    ExecutionKind,
    HypothesisTest,
    HypothesisTestDraft,
    HypothesisTestStatus,
    TemporalConsistencyParameters,
)
from casezero_investigation.hypotheses import TestStrength as Strength
from casezero_investigation.hypotheses import TestType as FalsificationType
from casezero_investigation.models import (
    Claim,
    ClaimStatus,
    Hypothesis,
    HypothesisStatus,
    TimelineEvent,
    TimePrecision,
)
from pydantic import ValidationError

CASE_ID = UUID(int=1)
INVESTIGATION_ID = UUID(int=2)
MODEL_RUN_ID = UUID(int=3)
JOB_ID = UUID(int=4)
EVIDENCE_ID = UUID(int=101)
CLAIM_ID = UUID(int=201)
BEFORE_ID = UUID(int=301)
AFTER_ID = UUID(int=302)
HYPOTHESIS_ID = UUID(int=401)
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def hypothesis(**changes: object) -> Hypothesis:
    return Hypothesis.model_validate(
        {
            "id": HYPOTHESIS_ID,
            "investigation_id": INVESTIGATION_ID,
            "case_id": CASE_ID,
            "title": "Synthetic interrupted flow hypothesis",
            "description": "A restriction might explain the synthetic measurement.",
            "initial_confidence": Decimal("0.4000"),
            "current_confidence": Decimal("0.4000"),
            "status": HypothesisStatus.ACTIVE,
            "distinguishing_prediction": "Independent flow records would show a restriction.",
            "weakening_evidence": "Uninterrupted flow would weaken this explanation.",
            "model_run_id": UUID(int=5),
            "created_at": NOW - timedelta(minutes=1),
        }
        | changes
    )


def evidence(**changes: object) -> EvidenceItem:
    return EvidenceItem.model_validate(
        {
            "id": EVIDENCE_ID,
            "case_id": CASE_ID,
            "source_document_id": UUID(int=1001),
            "type": EvidenceType.TEXT,
            "subtype": "flow measurement",
            "observation": "Synthetic unit-test measurement.",
            "source_locator": TextLocator(start=0, end=30),
            "extraction_method": ExtractionMethod.DETERMINISTIC,
        }
        | changes
    )


def claim(**changes: object) -> Claim:
    return Claim.model_validate(
        {
            "id": CLAIM_ID,
            "investigation_id": INVESTIGATION_ID,
            "case_id": CASE_ID,
            "text": "The synthetic measurement may indicate uninterrupted flow.",
            "status": ClaimStatus.INFERRED,
            "confidence": Decimal("0.6000"),
            "source_candidate_ids": (UUID(int=501),),
            "supporting_evidence_ids": (EVIDENCE_ID,),
            "contradicting_evidence_ids": (),
            "model_run_id": UUID(int=5),
            "created_at": NOW - timedelta(minutes=1),
        }
        | changes
    )


def event(identifier: UUID = BEFORE_ID, **changes: object) -> TimelineEvent:
    return TimelineEvent.model_validate(
        {
            "id": identifier,
            "investigation_id": INVESTIGATION_ID,
            "case_id": CASE_ID,
            "occurred_at": NOW - timedelta(hours=1),
            "time_precision": TimePrecision.EXACT,
            "description": "A synthetic recorder sample was captured.",
            "confidence": Decimal("0.8000"),
            "source_candidate_ids": (UUID(int=502),),
            "evidence_ids": (EVIDENCE_ID,),
            "model_run_id": UUID(int=5),
            "created_at": NOW - timedelta(minutes=1),
        }
        | changes
    )


def proposal(**changes: object) -> HypothesisTestDraft:
    return HypothesisTestDraft.model_validate(
        {
            "type": FalsificationType.EVIDENCE_PRESENCE,
            "expected_observation": "Independent flow measurements should show a restriction.",
            "strength": Strength.HIGH,
            "execution_kind": ExecutionKind.DETERMINISTIC,
            "parameters": {"schema_version": "evidence-presence-v1", "subtype": "flow measurement"},
            "evidence_ids": (EVIDENCE_ID,),
            "claim_ids": (CLAIM_ID,),
        }
        | changes
    )


def temporal_proposal(**changes: object) -> HypothesisTestDraft:
    return proposal(
        type=FalsificationType.TEMPORAL_CONSISTENCY,
        parameters={
            "schema_version": "temporal-consistency-v1",
            "before_event_id": str(BEFORE_ID),
            "after_event_id": str(AFTER_ID),
        } | changes,
    )


def draft(**changes: object) -> CritiqueDraft:
    return CritiqueDraft.model_validate(
        {
            "hypothesis_id": HYPOTHESIS_ID,
            "strongest_contradiction_id": EVIDENCE_ID,
            "missing_evidence": ("An independent flow measurement is unavailable.",),
            "alternative_explanation": "A measurement artifact remains possible.",
            "critique_confidence": 0.70005,
            "proposed_tests": (proposal(),),
        }
        | changes
    )


def test_critic_materializes_pending_tests_with_code_owned_ids_and_separate_provenance() -> None:
    output = draft(proposed_tests=(
        proposal(),
        temporal_proposal(),
        proposal(type=FalsificationType.CLAIM_CONTRADICTION,
                 parameters={"schema_version": "claim-contradiction-v1"}),
        proposal(type=FalsificationType.SEMANTIC_COMPARISON,
                 execution_kind=ExecutionKind.AI,
                 parameters={"schema_version": "semantic-comparison-v1"}),
    ))
    target = hypothesis()
    active: tuple[EvidenceItem, ...] = (evidence(),)
    claims: tuple[Claim, ...] = (claim(),)
    timeline: tuple[TimelineEvent, ...] = (event(), event(AFTER_ID))
    before = (output.model_dump(), target.model_dump(), active[0].model_dump(), claims[0].model_dump())
    with localcontext() as context:
        context.rounding = ROUND_UP
        bundle = materialize_critique(
            output, target, active, claims, timeline, JOB_ID, MODEL_RUN_ID, NOW,
        )
    assert isinstance(bundle, CritiqueBundle) and isinstance(bundle.tests, tuple)
    critique = bundle.critique
    assert critique.id.version == 4 and critique.hypothesis_id == HYPOTHESIS_ID
    assert critique.investigation_id == INVESTIGATION_ID and critique.case_id == CASE_ID
    assert critique.model_run_id == MODEL_RUN_ID and critique.created_at == NOW
    assert critique.critique_confidence == Decimal("0.7000")
    assert critique.critique_confidence.as_tuple().exponent == -4
    assert critique.strongest_contradiction_id == EVIDENCE_ID
    assert critique.missing_evidence == output.missing_evidence
    assert critique.alternative_explanation == output.alternative_explanation
    assert critique.proposed_tests == output.proposed_tests
    assert len(bundle.tests) == 4 and len({critique.id, *(item.id for item in bundle.tests)}) == 5
    for test, proposed in zip(bundle.tests, output.proposed_tests, strict=True):
        assert isinstance(test, HypothesisTest) and test.id.version == 4
        assert test.investigation_id == INVESTIGATION_ID and test.case_id == CASE_ID
        assert test.hypothesis_id == HYPOTHESIS_ID and test.critique_id == critique.id
        assert test.job_id == JOB_ID and test.created_at == NOW
        assert test.status is HypothesisTestStatus.PENDING
        assert test.outcome is None and test.completed_at is None and test.model_run_id is None
        assert test.type is proposed.type and test.execution_kind is proposed.execution_kind
        assert test.expected_observation == proposed.expected_observation
        assert test.typed_parameters == proposed.typed_parameters
        assert test.evidence_ids == proposed.evidence_ids and test.claim_ids == proposed.claim_ids
    assert isinstance(bundle.tests[0].typed_parameters, EvidencePresenceParameters)
    assert isinstance(bundle.tests[1].typed_parameters, TemporalConsistencyParameters)
    assert CritiqueBundle.model_validate_json(bundle.model_dump_json()) == bundle
    assert (output.model_dump(), target.model_dump(), active[0].model_dump(), claims[0].model_dump()) == before
    output.proposed_tests[0].parameters["subtype"] = "changed after materialization"
    assert bundle.tests[0].parameters["subtype"] == "flow measurement"
    assert critique.proposed_tests[0].parameters["subtype"] == "flow measurement"


@pytest.mark.parametrize("identifier", [EVIDENCE_ID, CLAIM_ID, None])
def test_strongest_contradiction_may_cite_only_supplied_claim_or_evidence(identifier: UUID | None) -> None:
    bundle = materialize_critique(
        draft(strongest_contradiction_id=identifier), hypothesis(), (evidence(),), (claim(),), (),
        JOB_ID, MODEL_RUN_ID, NOW,
    )
    assert bundle.critique.strongest_contradiction_id == identifier


def test_missing_evidence_can_be_tested_without_fabricating_references() -> None:
    output = draft(
        strongest_contradiction_id=None, alternative_explanation=None,
        proposed_tests=(proposal(evidence_ids=(), claim_ids=()),),
    )
    bundle = materialize_critique(output, hypothesis(), (), (), (), JOB_ID, MODEL_RUN_ID, NOW)
    assert bundle.tests[0].evidence_ids == bundle.tests[0].claim_ids == ()
    assert bundle.tests[0].status is HypothesisTestStatus.PENDING
    assert bundle.critique.strongest_contradiction_id is None


@pytest.mark.parametrize("field", ["hypothesis_id", "strongest_contradiction_id"])
def test_critic_rejects_unknown_target_and_contradiction_ids(field: str) -> None:
    with pytest.raises(ValueError, match="hypothesis|contradiction"):
        materialize_critique(
            draft(**{field: uuid4()}), hypothesis(), (evidence(),), (claim(),), (),
            JOB_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize("field", ["evidence_ids", "claim_ids"])
def test_critic_rejects_invented_or_inactive_test_references(field: str) -> None:
    with pytest.raises(ValueError, match="evidence|claim"):
        materialize_critique(
            draft(proposed_tests=(proposal(**{field: (uuid4(),)}),)),
            hypothesis(), (evidence(),), (claim(),), (), JOB_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize("field", ["before_event_id", "after_event_id"])
def test_critic_validates_temporal_parameter_event_ids(field: str) -> None:
    with pytest.raises(ValueError, match="timeline|event"):
        materialize_critique(
            draft(proposed_tests=(temporal_proposal(**{field: str(uuid4())}),)),
            hypothesis(), (evidence(),), (claim(),), (event(), event(AFTER_ID)),
            JOB_ID, MODEL_RUN_ID, NOW,
        )


def test_critic_keeps_unknown_temporal_events_unknown_and_does_not_execute_tests() -> None:
    timeline = (event(occurred_at=None, time_precision=TimePrecision.UNKNOWN), event(AFTER_ID))
    bundle = materialize_critique(
        draft(proposed_tests=(temporal_proposal(),)), hypothesis(), (evidence(),), (claim(),), timeline,
        JOB_ID, MODEL_RUN_ID, NOW,
    )
    assert timeline[0].occurred_at is None
    assert bundle.tests[0].outcome is None and bundle.tests[0].status is HypothesisTestStatus.PENDING


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("evidence", "case_id"),
        ("claim", "case_id"),
        ("claim", "investigation_id"),
        ("timeline", "case_id"),
        ("timeline", "investigation_id"),
    ],
)
def test_critic_rejects_foreign_state_even_when_unreferenced(target: str, field: str) -> None:
    active: tuple[EvidenceItem, ...] = (evidence(),)
    claims: tuple[Claim, ...] = (claim(),)
    timeline: tuple[TimelineEvent, ...] = (event(), event(AFTER_ID))
    if target == "evidence":
        active = (evidence(), evidence(id=uuid4(), **{field: uuid4()}))
    elif target == "claim":
        claims = (claim(), claim(id=uuid4(), **{field: uuid4()}))
    else:
        timeline = (*timeline, event(uuid4(), **{field: uuid4()}))
    with pytest.raises(ValueError, match="context|case|investigation"):
        materialize_critique(draft(), hypothesis(), active, claims, timeline, JOB_ID, MODEL_RUN_ID, NOW)


@pytest.mark.parametrize("target", ["evidence", "claim", "timeline"])
def test_critic_rejects_duplicate_state_ids(target: str) -> None:
    active = (evidence(), evidence()) if target == "evidence" else (evidence(),)
    claims = (claim(), claim()) if target == "claim" else (claim(),)
    timeline = (event(), event()) if target == "timeline" else (event(),)
    with pytest.raises(ValueError, match="duplicate"):
        materialize_critique(draft(), hypothesis(), active, claims, timeline, JOB_ID, MODEL_RUN_ID, NOW)


@pytest.mark.parametrize("field", ["supporting_evidence", "supporting_evidence_ids", "supporting_claim_ids", "support"])
def test_critic_contract_has_no_support_field(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        draft(**{field: (EVIDENCE_ID,)})
    assert not any("support" in name for name in CritiqueDraft.model_fields)


@pytest.mark.parametrize("field", ["id", "investigation_id", "case_id", "model_run_id", "created_at"])
def test_critic_cannot_choose_canonical_context_or_test_ids(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        draft(**{field: uuid4()})
    with pytest.raises(ValidationError, match="Extra inputs"):
        proposal(**{field: uuid4()})


@pytest.mark.parametrize("count", [0, 6])
def test_critic_requires_one_to_five_tests(count: int) -> None:
    with pytest.raises(ValidationError, match="at least 1|at most 5"):
        draft(proposed_tests=tuple(proposal() for _ in range(count)))


def test_critic_accepts_five_nonempty_tests() -> None:
    output = draft(proposed_tests=tuple(
        proposal(expected_observation=f"Independent synthetic measurement {index} should agree.")
        for index in range(5)
    ))
    assert len(materialize_critique(
        output, hypothesis(), (evidence(),), (claim(),), (), JOB_ID, MODEL_RUN_ID, NOW,
    ).tests) == 5


def test_critic_rejects_blank_tests_missing_evidence_and_alternatives() -> None:
    with pytest.raises(ValidationError):
        draft(proposed_tests=(proposal(expected_observation=" \t"),))
    with pytest.raises(ValidationError):
        draft(missing_evidence=(" \t",))
    with pytest.raises(ValidationError):
        draft(alternative_explanation=" \t")


@pytest.mark.parametrize("parameters", [
    {"schema_version": "unknown-v1"},
    {"schema_version": "evidence-presence-v1", "query": "arbitrary"},
    {"schema_version": "evidence-presence-v1", "subtype": " \t"},
    {"schema_version": "temporal-consistency-v1", "before_event_id": str(BEFORE_ID)},
])
def test_critic_uses_existing_closed_typed_test_parameters(parameters: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        draft(proposed_tests=(proposal(parameters=parameters),))


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf"), "0.7", True])
def test_critique_confidence_is_bounded_numeric_model_output(confidence: object) -> None:
    with pytest.raises(ValidationError):
        draft(critique_confidence=confidence)


def test_critic_contracts_are_strict_tuple_models() -> None:
    output = draft()
    assert CritiqueDraft.model_validate_json(output.model_dump_json()) == output
    with pytest.raises(ValidationError):
        draft(proposed_tests=[proposal()])
    with pytest.raises(ValidationError):
        draft(missing_evidence=["An independent measurement is missing."])
    bundle = materialize_critique(
        output, hypothesis(), (evidence(),), (claim(),), (), JOB_ID, MODEL_RUN_ID, NOW,
    )
    with pytest.raises(ValidationError):
        CritiqueBundle.model_validate(bundle.model_dump() | {"tests": list(bundle.tests)})
    with pytest.raises(ValidationError, match="Extra inputs"):
        CritiqueBundle.model_validate(bundle.model_dump() | {"supporting_evidence_ids": (EVIDENCE_ID,)})


@pytest.mark.parametrize("created_at", [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=2)))])
def test_critic_requires_aware_utc_context(created_at: datetime) -> None:
    with pytest.raises(ValueError, match="UTC-aware"):
        materialize_critique(
            draft(), hypothesis(), (evidence(),), (claim(),), (), JOB_ID, MODEL_RUN_ID, created_at,
        )


def test_critic_revalidates_mutated_nested_parameters_and_upstream_timestamps() -> None:
    output = draft(proposed_tests=(temporal_proposal(),))
    output.proposed_tests[0].parameters["after_event_id"] = str(uuid4())
    with pytest.raises(ValueError, match="timeline|event"):
        materialize_critique(
            output, hypothesis(), (evidence(),), (claim(),), (event(), event(AFTER_ID)),
            JOB_ID, MODEL_RUN_ID, NOW,
        )
    output = draft()
    output.proposed_tests[0].parameters["unsupported"] = True
    with pytest.raises(ValidationError):
        materialize_critique(
            output, hypothesis(), (evidence(),), (claim(),), (), JOB_ID, MODEL_RUN_ID, NOW,
        )
    invalid_event = event()
    invalid_event.occurred_at = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="UTC-aware"):
        materialize_critique(
            draft(), hypothesis(), (evidence(),), (claim(),), (invalid_event,),
            JOB_ID, MODEL_RUN_ID, NOW,
        )


def test_critic_prompt_is_versioned_and_requires_falsification_not_restatement_of_support() -> None:
    assert CRITIC_PROMPT.startswith("casezero.critic.v1:")
    for instruction in (
        "provisional", "not an official finding", "cite", "ids", "unknown", "blame",
        "weaken", "missing", "do not restate support", "5",
    ):
        assert instruction in CRITIC_PROMPT.casefold()
    assert "CEN22FA375" not in CRITIC_PROMPT
