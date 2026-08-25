from dataclasses import dataclass, field
from typing import Protocol

from casezero_evidence import (
    DerivedArtifactKind,
    DocumentType,
    ProcessingSource,
    SourceLocator,
    StructuralUnitKind,
)
from pydantic import JsonValue

from casezero_ingestion.media import DetectedMediaType


@dataclass(frozen=True, slots=True)
class StructuralUnitDraft:
    kind: StructuralUnitKind
    ordinal: int
    content_checksum: str
    locator: SourceLocator
    payload: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class StructuralOutput:
    artifact_kind: DerivedArtifactKind
    artifact_bytes: bytes
    media_type: str
    units: tuple[StructuralUnitDraft, ...]
    tool_metadata: dict[str, JsonValue] = field(default_factory=dict)


class Processor(Protocol):
    name: str
    version: str

    def supports(self, media_type: DetectedMediaType, document_type: DocumentType) -> bool: ...

    def process(self, source: ProcessingSource) -> StructuralOutput: ...
