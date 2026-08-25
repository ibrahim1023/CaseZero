from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class BoundingBox(StrictModel):
    x1: float = Field(ge=0)
    y1: float = Field(ge=0)
    x2: float = Field(gt=0)
    y2: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "BoundingBox":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("bounding box coordinates must be ordered")
        return self


class ReviewStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class Visibility(StrEnum):
    INVESTIGATION_EVIDENCE = "INVESTIGATION_EVIDENCE"
    OFFICIAL_ANALYSIS = "OFFICIAL_ANALYSIS"
    FINAL_FINDING = "FINAL_FINDING"


class DocumentType(StrEnum):
    FACTUAL_REPORT = "FACTUAL_REPORT"
    WITNESS_STATEMENT = "WITNESS_STATEMENT"
    CREW_STATEMENT = "CREW_STATEMENT"
    MAINTENANCE_RECORD = "MAINTENANCE_RECORD"
    WEATHER_RECORD = "WEATHER_RECORD"
    NOTAM = "NOTAM"
    TELEMETRY = "TELEMETRY"
    FLIGHT_DATA = "FLIGHT_DATA"
    IMAGE = "IMAGE"
    DIAGRAM = "DIAGRAM"
    EXAMINATION_REPORT = "EXAMINATION_REPORT"
    TECHNICAL_REPORT = "TECHNICAL_REPORT"
    MEDICAL_REPORT = "MEDICAL_REPORT"
    AUDIO = "AUDIO"
    TEXT = "TEXT"
    HTML = "HTML"
    FINAL_REPORT = "FINAL_REPORT"
    OTHER = "OTHER"


class EvidenceType(StrEnum):
    TEXT = "TEXT"
    TABLE = "TABLE"
    TIME_SERIES = "TIME_SERIES"
    IMAGE = "IMAGE"
    METADATA = "METADATA"


class ExtractionMethod(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI = "AI"
    HUMAN = "HUMAN"


class PdfLocator(StrictModel):
    kind: Literal["pdf"] = "pdf"
    page: int = Field(ge=1)
    paragraph: int | None = Field(default=None, ge=1)
    section: str | None = None
    bounding_box: BoundingBox | None = None
    reading_order: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def normalized_region(self) -> "PdfLocator":
        if self.bounding_box is not None and (
            self.bounding_box.x2 > 1 or self.bounding_box.y2 > 1
        ):
            raise ValueError("PDF bounding box must use normalized coordinates")
        return self


class TableLocator(StrictModel):
    kind: Literal["table"] = "table"
    row: int = Field(ge=1)
    row_end: int | None = Field(default=None, ge=1)
    columns: tuple[str, ...] = Field(min_length=1)
    sheet: str | None = None

    @model_validator(mode="after")
    def ordered_rows(self) -> "TableLocator":
        if self.row_end is not None and self.row_end < self.row:
            raise ValueError("row_end must not precede row")
        return self


class ImageLocator(StrictModel):
    kind: Literal["image"] = "image"
    image_id: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    region: BoundingBox | None = None

    @model_validator(mode="after")
    def region_within_dimensions(self) -> "ImageLocator":
        if self.region is not None and (
            self.region.x2 > self.width or self.region.y2 > self.height
        ):
            raise ValueError("image region must fit within dimensions")
        return self


class AudioLocator(StrictModel):
    kind: Literal["audio"] = "audio"
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> "AudioLocator":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class TextLocator(StrictModel):
    kind: Literal["text"] = "text"
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def end_follows_start(self) -> "TextLocator":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


SourceLocator = Annotated[
    PdfLocator | TableLocator | ImageLocator | AudioLocator | TextLocator,
    Field(discriminator="kind"),
]


class EntityReference(StrictModel):
    id: UUID
    type: str
    canonical_name: str


class SourceDocument(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    title: str = Field(min_length=1)
    source_url: AnyHttpUrl
    published_at: datetime | None = None
    evidence_date: datetime | None = None
    retrieved_at: datetime
    document_type: DocumentType
    visibility: Visibility
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("published_at", "evidence_date", "retrieved_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("datetime must be UTC-aware")
        return value


class EvidenceItem(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    source_document_id: UUID
    structural_unit_id: UUID | None = None
    model_run_id: UUID | None = None
    review_status: ReviewStatus = ReviewStatus.NOT_REQUIRED
    type: EvidenceType
    subtype: str | None = None
    observation: str = Field(min_length=1)
    source_locator: SourceLocator
    occurred_at: datetime | None = None
    entities: tuple[EntityReference, ...] = ()
    extraction_method: ExtractionMethod
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("occurred_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("datetime must be UTC-aware")
        return value
