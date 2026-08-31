from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from casezero_evidence import (
    DerivedArtifactKind,
    DocketItem,
    DocumentType,
    PdfLocator,
    ProcessingSource,
    RightsStatus,
    SourceDocument,
    StructuralUnitKind,
    Visibility,
)
from casezero_ingestion.pdf import (
    ParsedPdfBlock,
    ParsedPdfDocument,
    PdfLocatorAdapter,
    PdfProcessor,
    StructuralProcessingError,
)
from casezero_ingestion.scan_detection import PageSignals
from pydantic import AnyHttpUrl

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DOC_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")
NOW = datetime(2026, 8, 25, tzinfo=UTC)


@dataclass
class FixtureAdapter:
    parsed: ParsedPdfDocument | None

    def convert(self, data: bytes) -> ParsedPdfDocument:
        if self.parsed is None:
            raise StructuralProcessingError("invalid PDF")
        return self.parsed


def source() -> ProcessingSource:
    url = AnyHttpUrl("https://data.ntsb.gov/report.pdf")
    return ProcessingSource(
        docket_item=DocketItem(
            case_id=CASE_ID,
            title="NTSB report",
            source_url=url,
            document_type=DocumentType.FACTUAL_REPORT,
            rights_status=RightsStatus.NTSB_AUTHORED,
            attribution="Source: National Transportation Safety Board",
            review_note="fixture",
            reviewed_at=NOW,
        ),
        document=SourceDocument(
            id=DOC_ID,
            case_id=CASE_ID,
            title="NTSB report",
            source_url=url,
            retrieved_at=NOW,
            document_type=DocumentType.FACTUAL_REPORT,
            visibility=Visibility.INVESTIGATION_EVIDENCE,
            checksum="a" * 64,
        ),
        data=b"%PDF fixture",
    )


def test_pdf_locator_adapter_resolves_exact_reading_order_block() -> None:
    parsed = ParsedPdfDocument(
        blocks=(
            ParsedPdfBlock(page=1, reading_order=0, text="First paragraph"),
            ParsedPdfBlock(page=1, reading_order=1, text="Second paragraph"),
        ),
        page_signals={},
        structure_bytes=b"{}",
    )

    resolved = PdfLocatorAdapter(FixtureAdapter(parsed)).resolve(
        source().data, PdfLocator(page=1, reading_order=1)
    )

    assert resolved.media_type == "text/plain; charset=utf-8"
    assert resolved.content == b"Second paragraph"


def test_pdf_processor_preserves_page_order_and_scan_routing() -> None:
    parsed = ParsedPdfDocument(
        blocks=(
            ParsedPdfBlock(page=1, reading_order=0, text="First paragraph"),
            ParsedPdfBlock(page=1, reading_order=1, text="Second paragraph"),
        ),
        page_signals={
            1: PageSignals(character_count=31, image_coverage=0.1, has_fonts=True),
            2: PageSignals(character_count=0, image_coverage=1.0, has_fonts=False),
        },
        structure_bytes=b'{"pages":2}',
    )
    output = PdfProcessor(FixtureAdapter(parsed)).process(source())

    assert output.artifact_kind is DerivedArtifactKind.DOCUMENT_STRUCTURE
    assert [unit.kind for unit in output.units] == [
        StructuralUnitKind.TEXT_BLOCK,
        StructuralUnitKind.TEXT_BLOCK,
        StructuralUnitKind.IMAGE,
    ]
    assert isinstance(output.units[0].locator, PdfLocator)
    assert output.units[0].locator.reading_order == 0
    assert output.units[2].payload["requires_ocr"] is True


def test_pdf_processor_wraps_converter_failure() -> None:
    with pytest.raises(StructuralProcessingError, match="invalid PDF"):
        PdfProcessor(FixtureAdapter(None)).process(source())
