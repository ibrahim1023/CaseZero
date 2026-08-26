import hashlib
import io
import json

from casezero_evidence import (
    DerivedArtifactKind,
    DocumentType,
    ImageLocator,
    ProcessingSource,
    StructuralUnitKind,
)
from PIL import Image, UnidentifiedImageError
from pydantic import JsonValue

from casezero_ingestion.media import DetectedMediaType
from casezero_ingestion.processors import StructuralOutput, StructuralUnitDraft


class ImageProcessingError(ValueError):
    pass


class ImageProcessor:
    name = "image-preparation"
    version = "1.0.0"

    def supports(self, media_type: DetectedMediaType, document_type: DocumentType) -> bool:
        return media_type in {DetectedMediaType.PNG, DetectedMediaType.JPEG}

    def configuration(self) -> dict[str, JsonValue]:
        return {}

    def process(self, source: ProcessingSource) -> StructuralOutput:
        try:
            with Image.open(io.BytesIO(source.data)) as image:
                image.verify()
            with Image.open(io.BytesIO(source.data)) as image:
                width, height = image.size
                format_name = image.format or "unknown"
        except (UnidentifiedImageError, OSError) as error:
            raise ImageProcessingError("image cannot be decoded") from error
        payload: dict[str, JsonValue] = {
            "width": width,
            "height": height,
            "format": format_name,
        }
        artifact = (json.dumps(payload, sort_keys=True) + "\n").encode()
        unit = StructuralUnitDraft(
            kind=StructuralUnitKind.IMAGE,
            ordinal=0,
            content_checksum=hashlib.sha256(source.data).hexdigest(),
            locator=ImageLocator(image_id=str(source.document.id), width=width, height=height),
            payload=payload,
        )
        return StructuralOutput(
            DerivedArtifactKind.IMAGE_PREPARATION,
            artifact,
            "application/json",
            (unit,),
        )
