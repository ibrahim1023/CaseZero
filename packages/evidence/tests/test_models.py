from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from casezero_evidence import EvidenceItem as PublicEvidenceItem
from casezero_evidence.models import (
    DocumentType,
    EvidenceItem,
    EvidenceType,
    ExtractionMethod,
    PdfLocator,
    SourceDocument,
    Visibility,
)
from pydantic import AnyHttpUrl, ValidationError

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DOCUMENT_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")


def test_evidence_item_round_trips_with_pdf_locator() -> None:
    assert PublicEvidenceItem is EvidenceItem
    item = EvidenceItem(
        case_id=CASE_ID,
        source_document_id=DOCUMENT_ID,
        type=EvidenceType.TEXT,
        observation="Oil pressure fluctuated during climb.",
        source_locator=PdfLocator(page=17, paragraph=4),
        extraction_method=ExtractionMethod.DETERMINISTIC,
    )

    assert isinstance(item.source_locator, PdfLocator)
    assert item.source_locator.page == 17
    assert EvidenceItem.model_validate(item.model_dump()) == item


def test_source_document_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="UTC-aware"):
        SourceDocument(
            id=DOCUMENT_ID,
            case_id=CASE_ID,
            title="ATC factual report",
            source_url=AnyHttpUrl("https://data.ntsb.gov/example.pdf"),
            retrieved_at=datetime(2026, 8, 24, tzinfo=UTC).replace(tzinfo=None),
            document_type=DocumentType.FACTUAL_REPORT,
            visibility=Visibility.INVESTIGATION_EVIDENCE,
            checksum="0" * 64,
        )


def test_source_document_rejects_non_utc_datetime() -> None:
    non_utc = datetime(2026, 8, 24, tzinfo=timezone(timedelta(hours=4)))

    with pytest.raises(ValidationError, match="UTC-aware"):
        SourceDocument(
            id=DOCUMENT_ID,
            case_id=CASE_ID,
            title="ATC factual report",
            source_url=AnyHttpUrl("https://data.ntsb.gov/example.pdf"),
            retrieved_at=non_utc,
            document_type=DocumentType.FACTUAL_REPORT,
            visibility=Visibility.INVESTIGATION_EVIDENCE,
            checksum="0" * 64,
        )


def test_source_document_accepts_utc_datetime_and_sha256() -> None:
    document = SourceDocument(
        id=DOCUMENT_ID,
        case_id=CASE_ID,
        title="ATC factual report",
        source_url=AnyHttpUrl("https://data.ntsb.gov/example.pdf"),
        retrieved_at=datetime(2026, 8, 24, tzinfo=UTC),
        document_type=DocumentType.FACTUAL_REPORT,
        visibility=Visibility.INVESTIGATION_EVIDENCE,
        checksum="a" * 64,
    )

    assert document.retrieved_at.tzinfo is UTC


def test_source_document_rejects_invalid_checksum() -> None:
    with pytest.raises(ValidationError):
        SourceDocument(
            id=DOCUMENT_ID,
            case_id=CASE_ID,
            title="ATC factual report",
            source_url=AnyHttpUrl("https://data.ntsb.gov/example.pdf"),
            retrieved_at=datetime(2026, 8, 24, tzinfo=UTC),
            document_type=DocumentType.FACTUAL_REPORT,
            visibility=Visibility.INVESTIGATION_EVIDENCE,
            checksum="not-a-sha256",
        )


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PdfLocator(page=1, invented="value")  # type: ignore[call-arg]
