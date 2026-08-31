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
from casezero_evidence.locator import LocatorResolutionError, ResolvedRegion
from PIL import Image, UnidentifiedImageError
from pydantic import JsonValue

from casezero_ingestion.media import DetectedMediaType
from casezero_ingestion.processors import StructuralOutput, StructuralUnitDraft


class ImageProcessingError(ValueError):
    pass


class ImageLocatorAdapter:
    def supports(self, locator: object) -> bool:
        return isinstance(locator, ImageLocator)

    def resolve(self, source: bytes, locator: object) -> ResolvedRegion:
        if not isinstance(locator, ImageLocator):
            raise LocatorResolutionError("image adapter requires ImageLocator")
        try:
            with Image.open(io.BytesIO(source)) as image:
                if image.size != (locator.width, locator.height):
                    raise LocatorResolutionError("image dimensions do not match locator")
                if locator.region is None:
                    return ResolvedRegion(
                        media_type=Image.MIME.get(image.format or "", "application/octet-stream"),
                        content=source,
                    )
                region = locator.region
                cropped = image.crop(
                    (round(region.x1), round(region.y1), round(region.x2), round(region.y2))
                )
                output = io.BytesIO()
                cropped.save(output, format="PNG")
                return ResolvedRegion(media_type="image/png", content=output.getvalue())
        except (UnidentifiedImageError, OSError) as error:
            raise LocatorResolutionError("image source cannot be decoded") from error


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
