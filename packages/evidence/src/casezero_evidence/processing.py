from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, Field, JsonValue, field_validator, model_validator

from casezero_evidence.models import (
    BoundingBox,
    DocumentType,
    SourceDocument,
    SourceLocator,
    StrictModel,
)


class RightsStatus(StrEnum):
    NTSB_AUTHORED = "NTSB_AUTHORED"
    THIRD_PARTY_PERMISSION_CONFIRMED = "THIRD_PARTY_PERMISSION_CONFIRMED"
    THIRD_PARTY_UNCLEAR = "THIRD_PARTY_UNCLEAR"
    UNKNOWN = "UNKNOWN"


class ProcessingDisposition(StrEnum):
    AI_ALLOWED = "AI_ALLOWED"
    LOCAL_ONLY = "LOCAL_ONLY"
    LINK_ONLY = "LINK_ONLY"
    EXCLUDED = "EXCLUDED"


class ProcessingStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    SKIPPED_RIGHTS = "SKIPPED_RIGHTS"


class DerivedArtifactKind(StrEnum):
    DOCUMENT_STRUCTURE = "DOCUMENT_STRUCTURE"
    OCR_TRANSCRIPT = "OCR_TRANSCRIPT"
    TABLE_PROFILE = "TABLE_PROFILE"
    TIME_SERIES_PROFILE = "TIME_SERIES_PROFILE"
    IMAGE_PREPARATION = "IMAGE_PREPARATION"


class StructuralUnitKind(StrEnum):
    TEXT_BLOCK = "TEXT_BLOCK"
    TABLE = "TABLE"
    TABLE_ROW_GROUP = "TABLE_ROW_GROUP"
    TIME_SERIES_WINDOW = "TIME_SERIES_WINDOW"
    IMAGE = "IMAGE"


class DocketItem(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    title: str = Field(min_length=1)
    source_url: AnyHttpUrl
    document_type: DocumentType | None = None
    file_type: str | None = None
    page_count: int | None = Field(default=None, ge=1)
    published_at: datetime | None = None
    rights_status: RightsStatus
    processing_disposition: ProcessingDisposition | None = None
    attribution: str = Field(min_length=1)
    review_note: str = Field(min_length=1)
    reviewed_at: datetime
    expected_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("published_at", "reviewed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("datetime must be UTC-aware")
        return value

    @model_validator(mode="after")
    def enforce_rights_disposition(self) -> "DocketItem":
        unclear = self.rights_status in {
            RightsStatus.THIRD_PARTY_UNCLEAR,
            RightsStatus.UNKNOWN,
        }
        if self.processing_disposition is None:
            self.processing_disposition = (
                ProcessingDisposition.LINK_ONLY if unclear else ProcessingDisposition.AI_ALLOWED
            )
        if unclear and self.processing_disposition in {
            ProcessingDisposition.AI_ALLOWED,
            ProcessingDisposition.LOCAL_ONLY,
        }:
            raise ValueError("unclear rights require LINK_ONLY or EXCLUDED disposition")
        return self


class ProcessingSource(StrictModel):
    docket_item: DocketItem
    document: SourceDocument
    data: bytes


class ProcessingRun(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    source_document_id: UUID
    source_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    processor_name: str = Field(min_length=1)
    processor_version: str = Field(min_length=1)
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ProcessingStatus
    error_type: str | None = None
    error_message: str | None = None
    retryable: bool = False
    attempt_count: int = Field(default=1, ge=1)
    started_at: datetime
    completed_at: datetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("datetime must be UTC-aware")
        return value


class DerivedArtifact(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    processing_run_id: UUID
    source_document_id: UUID
    kind: DerivedArtifactKind
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    tool_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be UTC-aware")
        return value


class StructuralUnit(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    derived_artifact_id: UUID
    source_document_id: UUID
    kind: StructuralUnitKind
    ordinal: int = Field(ge=0)
    content_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: SourceLocator
    payload: dict[str, JsonValue]


__all__ = [
    "BoundingBox",
    "DerivedArtifact",
    "DerivedArtifactKind",
    "DocketItem",
    "ProcessingDisposition",
    "ProcessingRun",
    "ProcessingSource",
    "ProcessingStatus",
    "RightsStatus",
    "StructuralUnit",
    "StructuralUnitKind",
]
