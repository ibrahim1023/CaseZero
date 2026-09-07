from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, cast
from uuid import UUID

from casezero_evidence.models import DocumentType, EvidenceType, StrictModel
from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

NormalizedScore = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
MatchedFilter = Literal["evidence_type", "document_type", "entity", "time"]


class RetrievalIntent(StrEnum):
    SUPPORT = "SUPPORT"
    CONTRADICT = "CONTRADICT"
    NEUTRAL = "NEUTRAL"


class EvidenceSearchQuery(StrictModel):
    model_config = ConfigDict(frozen=True)

    investigation_id: UUID
    case_id: UUID
    intent: RetrievalIntent
    query_text: str = Field(min_length=1, max_length=4096)
    evidence_types: tuple[EvidenceType, ...] = ()
    document_types: tuple[DocumentType, ...] = ()
    entity_ids: tuple[UUID, ...] = ()
    start_at: datetime | None = None
    end_at: datetime | None = None
    limit: int = Field(default=10, ge=1, le=50)
    config_version: Literal["fts-v1"] = "fts-v1"

    @field_validator("query_text")
    @classmethod
    def require_searchable_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("query text must be non-blank and contain no NUL characters")
        return value

    @field_validator("evidence_types")
    @classmethod
    def order_evidence_types(cls, value: tuple[EvidenceType, ...]) -> tuple[EvidenceType, ...]:
        return tuple(sorted(set(value)))

    @field_validator("document_types")
    @classmethod
    def order_document_types(cls, value: tuple[DocumentType, ...]) -> tuple[DocumentType, ...]:
        return tuple(sorted(set(value)))

    @field_validator("entity_ids")
    @classmethod
    def order_entity_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return tuple(sorted(set(value)))

    @field_validator("start_at", "end_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be UTC-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_ordered_interval(self) -> "EvidenceSearchQuery":
        if self.start_at is not None and self.end_at is not None and self.end_at < self.start_at:
            raise ValueError("end_at must not precede start_at")
        return self

    def canonical_payload(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self.model_dump(mode="json"))


class EvidenceSearchResult(StrictModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: UUID
    rank: int = Field(ge=1, le=50)
    fts_score: NormalizedScore
    entity_score: NormalizedScore
    time_score: NormalizedScore
    type_score: NormalizedScore
    total_score: NormalizedScore
    matched_filters: tuple[MatchedFilter, ...] = ()


class RetrievalProbeCategory(StrEnum):
    COMPONENT_ENTITY_ALIASES = "COMPONENT_ENTITY_ALIASES"
    TEMPORAL_SEQUENCE = "TEMPORAL_SEQUENCE"
    TECHNICAL_SYMPTOM = "TECHNICAL_SYMPTOM"
    CONTRADICTION_ORIENTED = "CONTRADICTION_ORIENTED"


class RetrievalProbeSample(StrictModel):
    model_config = ConfigDict(frozen=True)

    query_id: str = Field(min_length=1)
    category: RetrievalProbeCategory
    expected_evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    baseline_evidence_ids: tuple[UUID, ...]
    hybrid_evidence_ids: tuple[UUID, ...]
