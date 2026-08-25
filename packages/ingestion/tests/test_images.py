import io
from datetime import UTC, datetime
from uuid import UUID

from casezero_evidence import (
    DocketItem,
    DocumentType,
    ImageLocator,
    ProcessingSource,
    RightsStatus,
    SourceDocument,
    Visibility,
)
from casezero_ingestion.images import ImageProcessor
from PIL import Image
from pydantic import AnyHttpUrl

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DOC_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")
NOW = datetime(2026, 8, 25, tzinfo=UTC)


def test_image_processor_records_dimensions_and_full_image_locator() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buffer, format="PNG")
    url = AnyHttpUrl("https://data.ntsb.gov/image.png")
    source = ProcessingSource(
        docket_item=DocketItem(case_id=CASE_ID, title="NTSB image", source_url=url, document_type=DocumentType.IMAGE, rights_status=RightsStatus.NTSB_AUTHORED, attribution="Source: National Transportation Safety Board", review_note="fixture", reviewed_at=NOW),
        document=SourceDocument(id=DOC_ID, case_id=CASE_ID, title="NTSB image", source_url=url, retrieved_at=NOW, document_type=DocumentType.IMAGE, visibility=Visibility.INVESTIGATION_EVIDENCE, checksum="a" * 64),
        data=buffer.getvalue(),
    )
    output = ImageProcessor().process(source)
    locator = output.units[0].locator
    assert isinstance(locator, ImageLocator)
    assert (locator.width, locator.height, locator.region) == (64, 48, None)
