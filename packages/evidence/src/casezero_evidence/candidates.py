from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from casezero_evidence.models import StrictModel


class ClaimStatus(StrEnum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    DISPUTED = "DISPUTED"
    UNKNOWN = "UNKNOWN"


class TimePrecision(StrEnum):
    EXACT = "EXACT"
    APPROXIMATE = "APPROXIMATE"
    RELATIVE = "RELATIVE"
    UNKNOWN = "UNKNOWN"


class CandidateModel(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    confidence: float = Field(ge=0, le=1)
    model_run_id: UUID
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be UTC-aware")
        return value


class ClaimCandidate(CandidateModel):
    text: str = Field(min_length=1)
    status: ClaimStatus
    supporting_evidence_ids: tuple[UUID, ...] = ()
    contradicting_evidence_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def require_evidence(self) -> "ClaimCandidate":
        if not self.supporting_evidence_ids and not self.contradicting_evidence_ids:
            raise ValueError("claim candidate requires evidence")
        return self


class EntityCandidate(CandidateModel):
    type: str = Field(min_length=1)
    proposed_canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)


class TimelineCandidate(CandidateModel):
    occurred_at: datetime | None
    time_precision: TimePrecision
    description: str = Field(min_length=1)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)

    @field_validator("occurred_at")
    @classmethod
    def require_occurred_at_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("datetime must be UTC-aware")
        return value

    @model_validator(mode="after")
    def exact_time_requires_value(self) -> "TimelineCandidate":
        if self.time_precision is TimePrecision.EXACT and self.occurred_at is None:
            raise ValueError("exact timeline candidate requires occurred_at")
        return self


__all__ = [
    "ClaimCandidate",
    "ClaimStatus",
    "EntityCandidate",
    "TimePrecision",
    "TimelineCandidate",
]
