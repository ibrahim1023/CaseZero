import re
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import overload
from uuid import UUID, uuid4

from casezero_evidence.models import StrictModel
from pydantic import Field, field_validator, model_validator

_FOUR_PLACES = Decimal("0.0001")
_WHITESPACE = re.compile(r"\s+")


class InvestigationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class InvestigationStage(StrEnum):
    PROMOTE_TIMELINE = "PROMOTE_TIMELINE"
    RESOLVE_ENTITIES = "RESOLVE_ENTITIES"
    PROMOTE_CLAIMS = "PROMOTE_CLAIMS"
    GENERATE_HYPOTHESES = "GENERATE_HYPOTHESES"
    SEARCH_SUPPORT = "SEARCH_SUPPORT"
    SEARCH_CONTRADICTIONS = "SEARCH_CONTRADICTIONS"
    DESIGN_FALSIFICATION_TESTS = "DESIGN_FALSIFICATION_TESTS"
    EXECUTE_FALSIFICATION_TESTS = "EXECUTE_FALSIFICATION_TESTS"
    REVISE_CONFIDENCE = "REVISE_CONFIDENCE"
    VERIFY_REPLAY = "VERIFY_REPLAY"
    COMPLETE = "COMPLETE"


class ClaimStatus(StrEnum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    DISPUTED = "DISPUTED"
    UNKNOWN = "UNKNOWN"


class HypothesisStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WEAKENED = "WEAKENED"
    REJECTED = "REJECTED"
    LEADING = "LEADING"


class LinkPolarity(StrEnum):
    SUPPORTING = "SUPPORTING"
    CONTRADICTING = "CONTRADICTING"


class TimePrecision(StrEnum):
    EXACT = "EXACT"
    APPROXIMATE = "APPROXIMATE"
    RELATIVE = "RELATIVE"
    UNKNOWN = "UNKNOWN"


class Investigation(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    status: InvestigationStatus
    current_stage: InvestigationStage
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_versions: dict[str, str] = Field(min_length=1)
    prompt_versions: dict[str, str] = Field(min_length=1)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("created_at", "started_at", "completed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)


class Claim(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    text: str = Field(min_length=1)
    status: ClaimStatus
    confidence: Decimal = Field(ge=0, le=1)
    source_candidate_ids: tuple[UUID, ...]
    supporting_evidence_ids: tuple[UUID, ...]
    contradicting_evidence_ids: tuple[UUID, ...]
    model_run_id: UUID
    created_at: datetime

    @field_validator("confidence")
    @classmethod
    def require_confidence_precision(cls, value: Decimal) -> Decimal:
        return _require_four_places(value)

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_lineage(self) -> "Claim":
        if not self.source_candidate_ids:
            raise ValueError("claim requires a source candidate")
        if not self.supporting_evidence_ids and not self.contradicting_evidence_ids:
            raise ValueError("claim requires evidence")
        return self


class InvestigationEntity(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    type: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    source_candidate_ids: tuple[UUID, ...] = Field(min_length=1)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    model_run_id: UUID
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @property
    def identity_aliases(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                _WHITESPACE.sub(" ", alias.strip()).casefold()
                for alias in self.aliases
                if alias.strip()
            )
        )


class TimelineEvent(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    occurred_at: datetime | None
    time_precision: TimePrecision
    description: str = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1)
    source_candidate_ids: tuple[UUID, ...] = Field(min_length=1)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    model_run_id: UUID
    created_at: datetime

    @field_validator("confidence")
    @classmethod
    def require_confidence_precision(cls, value: Decimal) -> Decimal:
        return _require_four_places(value)

    @field_validator("occurred_at", "created_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)


class Hypothesis(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    initial_confidence: Decimal = Field(ge=Decimal("0.05"), le=Decimal("0.85"))
    current_confidence: Decimal = Field(ge=0, le=1)
    status: HypothesisStatus
    distinguishing_prediction: str = Field(min_length=1)
    weakening_evidence: str = Field(min_length=1)
    model_run_id: UUID
    created_at: datetime

    @field_validator("initial_confidence", "current_confidence")
    @classmethod
    def require_confidence_precision(cls, value: Decimal) -> Decimal:
        return _require_four_places(value)

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class UnresolvedQuestion(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    hypothesis_id: UUID
    text: str = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def _require_four_places(value: Decimal) -> Decimal:
    if value != value.quantize(_FOUR_PLACES):
        raise ValueError("confidence must use at most four decimal places")
    return value.quantize(_FOUR_PLACES)


@overload
def _require_utc(value: datetime) -> datetime: ...


@overload
def _require_utc(value: None) -> None: ...


def _require_utc(value: datetime | None) -> datetime | None:
    if value is not None and (
        value.tzinfo is None or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("datetime must be UTC-aware")
    return value
