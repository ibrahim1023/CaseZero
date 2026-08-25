import hashlib
import json
import re
from dataclasses import asdict, dataclass

from casezero_evidence import (
    DerivedArtifactKind,
    DocumentType,
    ProcessingSource,
    StructuralUnitKind,
    TextLocator,
)

from casezero_ingestion.media import DetectedMediaType
from casezero_ingestion.processors import StructuralOutput, StructuralUnitDraft


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    start: int
    end: int


def extract_html_text(source: str) -> tuple[TextBlock, ...]:
    excluded = [match.span() for match in re.finditer(r"(?is)<(script|style)\b.*?</\1>", source)]
    blocks: list[TextBlock] = []
    for match in re.finditer(r">([^<]+)<", source):
        start, end = match.start(1), match.end(1)
        if any(left <= start < right for left, right in excluded):
            continue
        text = match.group(1)
        if text.strip():
            blocks.append(TextBlock(text=text, start=start, end=end))
    return tuple(blocks)


class TextProcessor:
    name = "text-structure"
    version = "1.0.0"

    def supports(self, media_type: DetectedMediaType, document_type: DocumentType) -> bool:
        return media_type in {DetectedMediaType.TEXT, DetectedMediaType.HTML}

    def process(self, source: ProcessingSource) -> StructuralOutput:
        text = source.data.decode("utf-8")
        blocks = extract_html_text(text) if "<" in text else (TextBlock(text, 0, len(text)),)
        units = tuple(
            StructuralUnitDraft(
                kind=StructuralUnitKind.TEXT_BLOCK,
                ordinal=index,
                content_checksum=hashlib.sha256(block.text.encode()).hexdigest(),
                locator=TextLocator(start=block.start, end=block.end),
                payload={"text": block.text},
            )
            for index, block in enumerate(blocks)
        )
        artifact = (json.dumps([asdict(block) for block in blocks]) + "\n").encode()
        return StructuralOutput(DerivedArtifactKind.DOCUMENT_STRUCTURE, artifact, "application/json", units)
