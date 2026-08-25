from datetime import UTC, datetime
from uuid import UUID

import pytest
from casezero_evidence import (
    DocumentType,
    ImageLocator,
    PdfLocator,
    SourceDocument,
    Visibility,
)
from casezero_evidence.processing import (
    BoundingBox,
    DerivedArtifact,
    DerivedArtifactKind,
    DocketItem,
    ProcessingDisposition,
    ProcessingRun,
    ProcessingSource,
    ProcessingStatus,
    RightsStatus,
    StructuralUnit,
    StructuralUnitKind,
)
from pydantic import AnyHttpUrl, ValidationError

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DOCUMENT_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")
RUN_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e71")
ARTIFACT_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e72")
NOW = datetime(2026, 8, 25, tzinfo=UTC)


def docket_item(
    rights_status: RightsStatus,
    processing_disposition: ProcessingDisposition | None = None,
) -> DocketItem:
    values: dict[str, object] = {
        "case_id": CASE_ID,
        "title": "Historical photo",
        "source_url": AnyHttpUrl("https://data.ntsb.gov/photo.pdf"),
        "rights_status": rights_status,
        "attribution": "Source: National Transportation Safety Board",
        "review_note": "Rights reviewed for Phase 1",
        "reviewed_at": NOW,
    }
    if processing_disposition is not None:
        values["processing_disposition"] = processing_disposition
    return DocketItem.model_validate(values)


def source_document() -> SourceDocument:
    return SourceDocument(
        id=DOCUMENT_ID,
        case_id=CASE_ID,
        title="NTSB examination report",
        source_url=AnyHttpUrl("https://data.ntsb.gov/report.pdf"),
        retrieved_at=NOW,
        document_type=DocumentType.EXAMINATION_REPORT,
        visibility=Visibility.INVESTIGATION_EVIDENCE,
        checksum="a" * 64,
    )


def test_unclear_rights_defaults_to_link_only() -> None:
    item = docket_item(RightsStatus.THIRD_PARTY_UNCLEAR)

    assert item.processing_disposition is ProcessingDisposition.LINK_ONLY


def test_ntsb_authored_defaults_to_ai_allowed() -> None:
    item = docket_item(RightsStatus.NTSB_AUTHORED)

    assert item.processing_disposition is ProcessingDisposition.AI_ALLOWED


def test_unclear_rights_rejects_ai_or_local_processing() -> None:
    for disposition in (ProcessingDisposition.AI_ALLOWED, ProcessingDisposition.LOCAL_ONLY):
        with pytest.raises(ValidationError, match="unclear rights"):
            docket_item(RightsStatus.THIRD_PARTY_UNCLEAR, disposition)


def test_processing_source_carries_reviewed_item_document_and_bytes() -> None:
    item = docket_item(RightsStatus.NTSB_AUTHORED)
    source = ProcessingSource(docket_item=item, document=source_document(), data=b"source")

    assert source.data == b"source"
    assert source.document.checksum == "a" * 64


def test_bounding_box_rejects_reversed_coordinates() -> None:
    with pytest.raises(ValidationError, match="ordered"):
        BoundingBox(x1=20, y1=10, x2=5, y2=30)


def test_pdf_locator_requires_normalized_region() -> None:
    with pytest.raises(ValidationError, match="normalized"):
        PdfLocator(page=1, bounding_box=BoundingBox(x1=0, y1=0, x2=2, y2=1))


def test_image_locator_rejects_region_outside_dimensions() -> None:
    with pytest.raises(ValidationError, match="dimensions"):
        ImageLocator(
            image_id="IMG-1",
            width=100,
            height=80,
            region=BoundingBox(x1=0, y1=0, x2=101, y2=40),
        )


def test_processing_ledger_models_round_trip() -> None:
    run = ProcessingRun(
        id=RUN_ID,
        source_document_id=DOCUMENT_ID,
        source_checksum="a" * 64,
        processor_name="fixture",
        processor_version="1.0.0",
        configuration_hash="b" * 64,
        status=ProcessingStatus.RUNNING,
        started_at=NOW,
    )
    artifact = DerivedArtifact(
        id=ARTIFACT_ID,
        processing_run_id=run.id,
        source_document_id=DOCUMENT_ID,
        kind=DerivedArtifactKind.DOCUMENT_STRUCTURE,
        checksum="c" * 64,
        storage_path="cc/" + "c" * 64,
        media_type="application/json",
        byte_size=12,
        created_at=NOW,
    )
    unit = StructuralUnit(
        derived_artifact_id=artifact.id,
        source_document_id=DOCUMENT_ID,
        kind=StructuralUnitKind.TEXT_BLOCK,
        ordinal=0,
        content_checksum="d" * 64,
        locator=PdfLocator(page=1, reading_order=0),
        payload={"text": "Visible source text"},
    )

    assert ProcessingRun.model_validate(run.model_dump()) == run
    assert DerivedArtifact.model_validate(artifact.model_dump()) == artifact
    assert StructuralUnit.model_validate(unit.model_dump()) == unit
