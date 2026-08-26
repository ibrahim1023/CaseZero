from dataclasses import dataclass

import pytest
from casezero_evidence import DocumentType, ProcessingSource
from casezero_ingestion.media import DetectedMediaType
from casezero_ingestion.processors import StructuralOutput
from casezero_ingestion.registry import (
    AmbiguousProcessorError,
    ProcessorRegistry,
    UnsupportedMediaError,
)


@dataclass
class FixtureProcessor:
    name: str
    version: str
    media_type: DetectedMediaType

    def supports(self, media_type: DetectedMediaType, document_type: DocumentType) -> bool:
        return media_type is self.media_type

    def configuration(self) -> dict[str, object]:
        return {}

    def process(self, source: ProcessingSource) -> StructuralOutput:
        raise NotImplementedError


def test_registry_selects_exactly_one_processor() -> None:
    pdf = FixtureProcessor("pdf", "1.0.0", DetectedMediaType.PDF)
    registry = ProcessorRegistry((pdf,))

    assert registry.select(DetectedMediaType.PDF, DocumentType.FACTUAL_REPORT) is pdf


def test_registry_rejects_unsupported_media() -> None:
    registry = ProcessorRegistry(())

    with pytest.raises(UnsupportedMediaError):
        registry.select(DetectedMediaType.PDF, DocumentType.FACTUAL_REPORT)


def test_registry_rejects_ambiguous_processors() -> None:
    registry = ProcessorRegistry(
        (
            FixtureProcessor("pdf-a", "1.0.0", DetectedMediaType.PDF),
            FixtureProcessor("pdf-b", "1.0.0", DetectedMediaType.PDF),
        )
    )

    with pytest.raises(AmbiguousProcessorError):
        registry.select(DetectedMediaType.PDF, DocumentType.FACTUAL_REPORT)
