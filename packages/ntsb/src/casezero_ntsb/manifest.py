from datetime import datetime, timedelta
from pathlib import Path

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class ManifestModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", populate_by_name=True)


class DocketDocument(ManifestModel):
    title: str = Field(min_length=1, description="Document title shown in the NTSB docket.")
    document_type: str | None = Field(
        default=None,
        alias="documentType",
        description="Document type or category shown by the NTSB docket.",
    )
    source_url: AnyHttpUrl = Field(
        alias="sourceUrl",
        description="Direct public URL for downloading the original docket artifact.",
    )
    file_type: str | None = Field(
        default=None,
        alias="fileType",
        description="File extension or media type when shown by the docket.",
    )
    page_count: int | None = Field(
        default=None,
        alias="pageCount",
        ge=1,
        description="Number of pages when shown by the docket.",
    )
    published_at: datetime | None = Field(
        default=None,
        alias="publishedAt",
        description="UTC publication timestamp when shown by the docket.",
    )

    @field_validator("published_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("published_at must be UTC-aware")
        return value


class DocketManifest(ManifestModel):
    case_id: str = Field(
        alias="caseId",
        min_length=1,
        description="NTSB investigation number for this docket.",
    )
    documents: tuple[DocketDocument, ...] = Field(
        min_length=1,
        description="Public source artifacts listed in the NTSB docket.",
    )


def load_manifest(path: Path) -> DocketManifest:
    suffix = path.suffix.casefold()
    if suffix == ".json":
        return DocketManifest.model_validate_json(path.read_bytes())
    if suffix in {".yaml", ".yml"}:
        return DocketManifest.model_validate(yaml.safe_load(path.read_text()), strict=False)
    raise ValueError("curated docket manifest must be JSON or YAML")
