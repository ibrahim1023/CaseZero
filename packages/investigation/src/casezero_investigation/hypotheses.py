import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from casezero_evidence.models import EvidenceType, StrictModel
from pydantic import Field, JsonValue, field_validator, model_validator

from casezero_investigation.models import _require_four_places, _require_utc


class TestType(StrEnum):
    EVIDENCE_PRESENCE = "EVIDENCE_PRESENCE"
    TEMPORAL_CONSISTENCY = "TEMPORAL_CONSISTENCY"
    CLAIM_CONTRADICTION = "CLAIM_CONTRADICTION"
    SEMANTIC_COMPARISON = "SEMANTIC_COMPARISON"


class TestStrength(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TestOutcome(StrEnum):
    CONTRADICTED = "CONTRADICTED"
    SURVIVED = "SURVIVED"
    EXPECTED_EVIDENCE_MISSING = "EXPECTED_EVIDENCE_MISSING"
    INCONCLUSIVE = "INCONCLUSIVE"


class ExecutionKind(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI = "AI"


class HypothesisTestStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class EvidencePresenceParameters(StrictModel):
    schema_version: Literal["evidence-presence-v1"]
    evidence_type: EvidenceType | None = None
    subtype: str | None = Field(default=None, min_length=1)

    @field_validator("subtype")
    @classmethod
    def require_nonblank_subtype(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("subtype must not be blank")
        return value


class TemporalConsistencyParameters(StrictModel):
    schema_version: Literal["temporal-consistency-v1"]
    before_event_id: UUID
    after_event_id: UUID

    @model_validator(mode="after")
    def require_distinct_events(self) -> "TemporalConsistencyParameters":
        if self.before_event_id == self.after_event_id:
            raise ValueError("temporal test requires distinct event IDs")
        return self


class ClaimContradictionParameters(StrictModel):
    schema_version: Literal["claim-contradiction-v1"]


class SemanticComparisonParameters(StrictModel):
    schema_version: Literal["semantic-comparison-v1"]


type TestParameters = (
    EvidencePresenceParameters
    | TemporalConsistencyParameters
    | ClaimContradictionParameters
    | SemanticComparisonParameters
)

_PARAMETER_MODELS: dict[TestType, type[TestParameters]] = {
    TestType.EVIDENCE_PRESENCE: EvidencePresenceParameters,
    TestType.TEMPORAL_CONSISTENCY: TemporalConsistencyParameters,
    TestType.CLAIM_CONTRADICTION: ClaimContradictionParameters,
    TestType.SEMANTIC_COMPARISON: SemanticComparisonParameters,
}


def _parse_parameters(test_type: TestType, parameters: dict[str, JsonValue]) -> TestParameters:
    return _PARAMETER_MODELS[test_type].model_validate_json(json.dumps(parameters))


class HypothesisTestDraft(StrictModel):
    type: TestType
    expected_observation: str = Field(min_length=1)
    strength: TestStrength
    execution_kind: ExecutionKind
    parameters: dict[str, JsonValue]
    evidence_ids: tuple[UUID, ...]
    claim_ids: tuple[UUID, ...]

    @property
    def typed_parameters(self) -> TestParameters:
        return _parse_parameters(self.type, self.parameters)

    @field_validator("evidence_ids", "claim_ids")
    @classmethod
    def require_unique_references(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("test reference IDs must be unique")
        return value

    @model_validator(mode="after")
    def require_execution_kind(self) -> "HypothesisTestDraft":
        if self.type is TestType.SEMANTIC_COMPARISON:
            if self.execution_kind is not ExecutionKind.AI:
                raise ValueError("SEMANTIC_COMPARISON requires AI execution")
        elif self.execution_kind is ExecutionKind.AI:
            raise ValueError("deterministic test cannot use AI execution")
        _parse_parameters(self.type, self.parameters)
        return self


class HypothesisCritique(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    hypothesis_id: UUID
    strongest_contradiction_id: UUID | None = None
    missing_evidence: tuple[str, ...]
    alternative_explanation: str | None = None
    critique_confidence: Decimal = Field(ge=0, le=1)
    proposed_tests: tuple[HypothesisTestDraft, ...]
    model_run_id: UUID
    created_at: datetime

    @field_validator("critique_confidence")
    @classmethod
    def require_confidence_precision(cls, value: Decimal) -> Decimal:
        return _require_four_places(value)

    @field_validator("created_at")
    @classmethod
    def require_created_at_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class HypothesisTest(HypothesisTestDraft):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    hypothesis_id: UUID
    critique_id: UUID
    job_id: UUID
    status: HypothesisTestStatus
    outcome: TestOutcome | None = None
    model_run_id: UUID | None = None
    created_at: datetime
    completed_at: datetime | None = None

    @field_validator("created_at", "completed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_result_context(self) -> "HypothesisTest":
        if self.status is HypothesisTestStatus.SUCCEEDED and (
            self.completed_at is None or self.outcome is None
        ):
            raise ValueError("successful test requires completed_at and outcome")
        if self.status is HypothesisTestStatus.PENDING and (
            self.completed_at is not None or self.outcome is not None
        ):
            raise ValueError("pending test cannot have completed_at or outcome")
        if self.status is HypothesisTestStatus.FAILED:
            if self.completed_at is None:
                raise ValueError("failed test requires completed_at")
            if self.outcome is not None:
                raise ValueError("failed test cannot have outcome")
        if (
            self.execution_kind is ExecutionKind.AI
            and self.status is not HypothesisTestStatus.PENDING
            and self.model_run_id is None
        ):
            raise ValueError("AI test requires model_run_id")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("test completion cannot precede creation")
        if self.execution_kind is ExecutionKind.DETERMINISTIC and self.model_run_id is not None:
            raise ValueError("deterministic test cannot reference model_run_id")
        return self


class TestDelta(StrictModel):
    test_id: UUID
    delta: Decimal = Field(ge=Decimal("-0.35"), le=Decimal("0.06"))

    @field_validator("delta")
    @classmethod
    def require_delta_precision(cls, value: Decimal) -> Decimal:
        return _require_four_places(value)


class ConfidenceRevision(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    hypothesis_id: UUID
    before: Decimal = Field(ge=0, le=1)
    delta: Decimal
    after: Decimal = Field(ge=Decimal("0.05"), le=Decimal("0.95"))
    rule_version: Literal["weighted-delta-v1"]
    test_deltas: tuple[TestDelta, ...]
    rationale: str = Field(min_length=1)
    created_at: datetime

    @field_validator("before", "delta", "after")
    @classmethod
    def require_precision(cls, value: Decimal) -> Decimal:
        return _require_four_places(value)

    @field_validator("created_at")
    @classmethod
    def require_created_at_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_calculation(self) -> "ConfidenceRevision":
        if len({item.test_id for item in self.test_deltas}) != len(self.test_deltas):
            raise ValueError("duplicate test IDs in confidence revision")
        ordered = tuple(sorted(self.test_deltas, key=lambda item: str(item.test_id)))
        if self.test_deltas != ordered:
            raise ValueError("test deltas must be ordered by test id")
        if sum((item.delta for item in ordered), Decimal("0.0000")) != self.delta:
            raise ValueError("revision delta must equal test delta sum")
        expected = min(Decimal("0.9500"), max(Decimal("0.0500"), self.before + self.delta))
        if expected != self.after:
            raise ValueError("revision after value must equal clamped sum")
        return self
