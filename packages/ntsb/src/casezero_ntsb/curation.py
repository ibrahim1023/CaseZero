from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from casezero_evidence import DocumentType, ProcessingDisposition, RightsStatus
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator


class CurationModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", populate_by_name=True)


class CuratedDocketItem(CurationModel):
    title: str = Field(min_length=1)
    source_url: AnyHttpUrl = Field(alias="sourceUrl")
    document_type: DocumentType | None = Field(default=None, alias="documentType")
    file_type: str | None = Field(default=None, alias="fileType")
    page_count: int | None = Field(default=None, alias="pageCount", ge=1)
    published_at: datetime | None = Field(default=None, alias="publishedAt")
    rights_status: RightsStatus = Field(alias="rightsStatus")
    processing_disposition: ProcessingDisposition = Field(alias="processingDisposition")
    attribution: str = Field(min_length=1)
    review_note: str = Field(alias="reviewNote", min_length=1)
    reviewed_at: datetime = Field(alias="reviewedAt")
    expected_checksum: str | None = Field(
        default=None, alias="expectedChecksum", pattern=r"^[0-9a-f]{64}$"
    )

    @field_validator("published_at", "reviewed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("datetime must be UTC-aware")
        return value

    @model_validator(mode="after")
    def enforce_rights(self) -> "CuratedDocketItem":
        if self.rights_status in {RightsStatus.THIRD_PARTY_UNCLEAR, RightsStatus.UNKNOWN} and (
            self.processing_disposition
            not in {ProcessingDisposition.LINK_ONLY, ProcessingDisposition.EXCLUDED}
        ):
            raise ValueError("unclear rights require LINK_ONLY or EXCLUDED")
        return self


class CuratedCaseManifest(CurationModel):
    case_id: str = Field(alias="caseId", min_length=1)
    docket_url: AnyHttpUrl = Field(alias="docketUrl")
    expected_item_count: int = Field(alias="expectedItemCount", ge=1)
    items: tuple[CuratedDocketItem, ...] = Field(min_length=1)


class CurationReport(CurationModel):
    valid: bool
    errors: tuple[str, ...]


def load_curated_manifest(path: Path) -> CuratedCaseManifest:
    return CuratedCaseManifest.model_validate_json(path.read_bytes())


def validate_curated_manifest(manifest: CuratedCaseManifest) -> CurationReport:
    errors: list[str] = []
    if len(manifest.items) != manifest.expected_item_count:
        errors.append(
            f"expected {manifest.expected_item_count} docket items, found {len(manifest.items)}"
        )
    seen_urls: set[str] = set()
    for index, item in enumerate(manifest.items, start=1):
        source_url = str(item.source_url)
        host = urlparse(source_url).hostname
        if host != "ntsb.gov" and (host is None or not host.endswith(".ntsb.gov")):
            errors.append(f"item {index} sourceUrl must use an ntsb.gov host")
        if source_url in seen_urls:
            errors.append(f"duplicate sourceUrl at item {index}")
        seen_urls.add(source_url)
        processable = item.processing_disposition in {
            ProcessingDisposition.AI_ALLOWED,
            ProcessingDisposition.LOCAL_ONLY,
        }
        if processable and item.expected_checksum is None:
            errors.append(f"processable item {index} is missing expectedChecksum")
        if not processable and item.expected_checksum is not None:
            errors.append(f"non-processable item {index} must not pin downloaded bytes")
    return CurationReport(valid=not errors, errors=tuple(errors))
