from dataclasses import dataclass, field
from typing import Protocol

from casezero_evidence import (
    DerivedArtifactKind,
    DocumentType,
    ProcessingSource,
    StructuralUnit,
)
from pydantic import JsonValue

from casezero_ingestion.media import DetectedMediaType


@dataclass(frozen=True, slots=True)
class StructuralOutput:
    artifact_kind: DerivedArtifactKind
    artifact_bytes: bytes
    media_type: str
    units: tuple[StructuralUnit, ...]
    tool_metadata: dict[str, JsonValue] = field(default_factory=dict)


class Processor(Protocol):
    name: str
    version: str

    def supports(self, media_type: DetectedMediaType, document_type: DocumentType) -> bool: ...

    def process(self, source: ProcessingSource) -> StructuralOutput: ...
