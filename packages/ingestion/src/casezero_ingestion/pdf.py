import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

from casezero_evidence import (
    BoundingBox,
    DerivedArtifactKind,
    DocumentType,
    PdfLocator,
    ProcessingSource,
    StructuralUnitKind,
)
from pydantic import JsonValue

from casezero_ingestion.media import DetectedMediaType
from casezero_ingestion.processors import StructuralOutput, StructuralUnitDraft
from casezero_ingestion.scan_detection import (
    PageKind,
    PageSignals,
    ScanDetectionConfig,
    classify_pdf_page,
)


class StructuralProcessingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedPdfBlock:
    page: int
    reading_order: int
    text: str
    section: str | None = None
    bounding_box: BoundingBox | None = None


@dataclass(frozen=True, slots=True)
class ParsedPdfDocument:
    blocks: tuple[ParsedPdfBlock, ...]
    page_signals: dict[int, PageSignals]
    structure_bytes: bytes


class PdfAdapter(Protocol):
    def convert(self, data: bytes) -> ParsedPdfDocument: ...


class DoclingPdfAdapter:
    def __init__(self) -> None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions(
            do_ocr=False,
            do_table_structure=True,
            generate_page_images=False,
            generate_picture_images=False,
        )
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )

    def convert(self, data: bytes) -> ParsedPdfDocument:
        from docling.datamodel.base_models import DocumentStream

        result = self._converter.convert(
            DocumentStream(name="source.pdf", stream=BytesIO(data)),
            raises_on_error=True,
        )
        exported = result.document.export_to_dict()
        pages = exported.get("pages", {})
        blocks: list[ParsedPdfBlock] = []
        character_counts: dict[int, int] = {}
        for order, text_item in enumerate(exported.get("texts", [])):
            text = str(text_item.get("text", ""))
            provenance = text_item.get("prov") or []
            if not provenance or not text:
                continue
            page = int(provenance[0]["page_no"])
            character_counts[page] = character_counts.get(page, 0) + len(text)
            blocks.append(
                ParsedPdfBlock(
                    page=page,
                    reading_order=order,
                    text=text,
                    section=str(text_item.get("label")) if text_item.get("label") else None,
                    bounding_box=_normalized_bbox(provenance[0].get("bbox"), pages, page),
                )
            )
        page_signals = {
            int(page_number): PageSignals(
                character_count=character_counts.get(int(page_number), 0),
                image_coverage=_picture_coverage(exported.get("pictures", []), pages, int(page_number)),
                has_fonts=character_counts.get(int(page_number), 0) > 0,
            )
            for page_number in pages
        }
        return ParsedPdfDocument(
            blocks=tuple(blocks),
            page_signals=page_signals,
            structure_bytes=(json.dumps(exported, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )


class PdfProcessor:
    name = "docling-pdf"
    version = "1.0.0"

    def __init__(self, adapter: PdfAdapter, scan_config: ScanDetectionConfig | None = None) -> None:
        self._adapter = adapter
        self._scan_config = scan_config or ScanDetectionConfig()

    def supports(self, media_type: DetectedMediaType, document_type: DocumentType) -> bool:
        return media_type is DetectedMediaType.PDF

    def configuration(self) -> dict[str, JsonValue]:
        return {
            "minimum_characters": self._scan_config.minimum_characters,
            "minimum_image_coverage": self._scan_config.minimum_image_coverage,
        }

    def process(self, source: ProcessingSource) -> StructuralOutput:
        parsed = self._adapter.convert(source.data)
        units: list[StructuralUnitDraft] = []
        for block in parsed.blocks:
            content = block.text.encode()
            units.append(
                StructuralUnitDraft(
                    kind=StructuralUnitKind.TEXT_BLOCK,
                    ordinal=len(units),
                    content_checksum=hashlib.sha256(content).hexdigest(),
                    locator=PdfLocator(
                        page=block.page,
                        section=block.section,
                        bounding_box=block.bounding_box,
                        reading_order=block.reading_order,
                    ),
                    payload={"text": block.text},
                )
            )
        for page, signals in sorted(parsed.page_signals.items()):
            if classify_pdf_page(signals, self._scan_config) is PageKind.SCAN_LIKE:
                payload: dict[str, JsonValue] = {"page": page, "requires_ocr": True}
                content = f"scan:{page}".encode()
                units.append(
                    StructuralUnitDraft(
                        kind=StructuralUnitKind.IMAGE,
                        ordinal=len(units),
                        content_checksum=hashlib.sha256(content).hexdigest(),
                        locator=PdfLocator(page=page),
                        payload=payload,
                    )
                )
        return StructuralOutput(
            artifact_kind=DerivedArtifactKind.DOCUMENT_STRUCTURE,
            artifact_bytes=parsed.structure_bytes,
            media_type="application/json",
            units=tuple(units),
            tool_metadata={"processor": self.name, "version": self.version},
        )


def _page_size(pages: object, page: int) -> tuple[float, float] | None:
    if not isinstance(pages, dict):
        return None
    value = pages.get(str(page), pages.get(page))
    if not isinstance(value, dict) or not isinstance(value.get("size"), dict):
        return None
    size = value["size"]
    try:
        return float(size["width"]), float(size["height"])
    except (KeyError, TypeError, ValueError):
        return None


def _normalized_bbox(bbox: object, pages: object, page: int) -> BoundingBox | None:
    size = _page_size(pages, page)
    if not isinstance(bbox, dict) or size is None:
        return None
    width, height = size
    try:
        return BoundingBox(
            x1=float(bbox["l"]) / width,
            y1=min(float(bbox["b"]), float(bbox["t"])) / height,
            x2=float(bbox["r"]) / width,
            y2=max(float(bbox["b"]), float(bbox["t"])) / height,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _picture_coverage(pictures: object, pages: object, page: int) -> float:
    size = _page_size(pages, page)
    if not isinstance(pictures, list) or size is None:
        return 0.0
    width, height = size
    area = 0.0
    for picture in pictures:
        if not isinstance(picture, dict):
            continue
        provenance = picture.get("prov") or []
        if not provenance or provenance[0].get("page_no") != page:
            continue
        bbox = provenance[0].get("bbox")
        if isinstance(bbox, dict):
            try:
                area += abs(float(bbox["r"]) - float(bbox["l"])) * abs(
                    float(bbox["t"]) - float(bbox["b"])
                )
            except (KeyError, TypeError, ValueError):
                continue
    return min(area / (width * height), 1.0)
