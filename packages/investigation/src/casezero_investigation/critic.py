from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from casezero_evidence.models import EvidenceItem, StrictModel
from pydantic import ConfigDict, Field, field_validator, model_validator, validate_call

from casezero_investigation.hypotheses import (
    HypothesisCritique,
    HypothesisTest,
    HypothesisTestDraft,
    HypothesisTestStatus,
    TemporalConsistencyParameters,
)
from casezero_investigation.models import (
    _FOUR_PLACES,
    Claim,
    Hypothesis,
    TimelineEvent,
    _require_utc,
)

CRITIC_PROMPT = (
    "casezero.critic.v1: AI-Generated - Not an Official Finding. Attempt to weaken the supplied "
    "provisional hypothesis; expert review is required. Do not assign blame, negligence, unlawful "
    "conduct, or liability to any person or company. Do not restate support. Cite only supplied "
    "hypothesis, claim, evidence, and timeline event IDs. Identify the strongest contradiction "
    "when present, missing expected evidence, and an optional alternative explanation. Keep "
    "unknowns unknown; do not invent evidence, absolute timestamps, or test outcomes. Propose "
    "1 to 5 nonempty falsification tests with explicit expected observations and the closed, "
    "versioned parameters for each test type. Evidence presence, temporal consistency, and "
    "claim contradiction use deterministic execution; semantic comparison requires a separate "
    "AI execution. Use bounded model confidence, not calibrated probability. Do not choose "
    "canonical IDs or creation context. Do not use external knowledge or official findings."
)


class CritiqueDraft(StrictModel):
    hypothesis_id: UUID
    strongest_contradiction_id: UUID | None = None
    missing_evidence: tuple[str, ...]
    alternative_explanation: str | None = Field(default=None, min_length=1)
    critique_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    proposed_tests: tuple[HypothesisTestDraft, ...] = Field(min_length=1, max_length=5)

    @field_validator("missing_evidence")
    @classmethod
    def require_missing_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not text.strip() for text in value):
            raise ValueError("missing evidence descriptions must not be blank")
        return value

    @field_validator("alternative_explanation")
    @classmethod
    def require_alternative(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("alternative explanation must not be blank")
        return value

    @model_validator(mode="after")
    def require_test_predictions(self) -> "CritiqueDraft":
        if any(not test.expected_observation.strip() for test in self.proposed_tests):
            raise ValueError("falsification tests require nonblank expected observations")
        return self


class CritiqueBundle(StrictModel):
    critique: HypothesisCritique
    tests: tuple[HypothesisTest, ...]


@validate_call(config=ConfigDict(strict=True))
def materialize_critique(
    output: CritiqueDraft,
    hypothesis: Hypothesis,
    evidence: tuple[EvidenceItem, ...],
    claims: tuple[Claim, ...],
    timeline: tuple[TimelineEvent, ...],
    job_id: UUID,
    model_run_id: UUID,
    created_at: datetime,
) -> CritiqueBundle:
    _require_utc(created_at)
    output = CritiqueDraft.model_validate(output.model_dump())
    hypothesis = Hypothesis.model_validate(hypothesis.model_dump())
    evidence = tuple(EvidenceItem.model_validate(item.model_dump()) for item in evidence)
    claims = tuple(Claim.model_validate(item.model_dump()) for item in claims)
    timeline = tuple(TimelineEvent.model_validate(item.model_dump()) for item in timeline)
    if output.hypothesis_id != hypothesis.id:
        raise ValueError("critique must reference the supplied hypothesis")
    evidence_ids = {item.id for item in evidence}
    claim_ids = {item.id for item in claims}
    event_ids = {item.id for item in timeline}
    if (
        len(evidence_ids) != len(evidence)
        or len(claim_ids) != len(claims)
        or len(event_ids) != len(timeline)
    ):
        raise ValueError("duplicate state IDs in critic inputs")
    canonical_state: tuple[Claim | TimelineEvent, ...] = (*claims, *timeline)
    if any(item.case_id != hypothesis.case_id for item in evidence) or any(
        item.case_id != hypothesis.case_id or item.investigation_id != hypothesis.investigation_id
        for item in canonical_state
    ):
        raise ValueError("critic inputs must match the hypothesis investigation and case context")
    if (
        output.strongest_contradiction_id is not None
        and output.strongest_contradiction_id not in evidence_ids | claim_ids
    ):
        raise ValueError("strongest contradiction must reference a supplied claim or active evidence")
    for test in output.proposed_tests:
        if not set(test.evidence_ids) <= evidence_ids:
            raise ValueError("test references unknown or inactive evidence IDs")
        if not set(test.claim_ids) <= claim_ids:
            raise ValueError("test references unknown claim IDs")
        parameters = test.typed_parameters
        if isinstance(parameters, TemporalConsistencyParameters) and not {
            parameters.before_event_id, parameters.after_event_id,
        } <= event_ids:
            raise ValueError("temporal test references unknown timeline event IDs")
    critique = HypothesisCritique(
        investigation_id=hypothesis.investigation_id,
        case_id=hypothesis.case_id,
        hypothesis_id=hypothesis.id,
        strongest_contradiction_id=output.strongest_contradiction_id,
        missing_evidence=output.missing_evidence,
        alternative_explanation=output.alternative_explanation,
        critique_confidence=Decimal(str(output.critique_confidence)).quantize(
            _FOUR_PLACES, rounding=ROUND_HALF_EVEN,
        ),
        proposed_tests=output.proposed_tests,
        model_run_id=model_run_id,
        created_at=created_at,
    )
    tests = tuple(
        HypothesisTest(
            **test.model_dump(),
            investigation_id=hypothesis.investigation_id,
            case_id=hypothesis.case_id,
            hypothesis_id=hypothesis.id,
            critique_id=critique.id,
            job_id=job_id,
            status=HypothesisTestStatus.PENDING,
            model_run_id=None,
            created_at=created_at,
        )
        for test in output.proposed_tests
    )
    return CritiqueBundle(critique=critique, tests=tests)
