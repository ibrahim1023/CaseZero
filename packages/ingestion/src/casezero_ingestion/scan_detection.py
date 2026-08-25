from dataclasses import dataclass
from enum import StrEnum


class PageKind(StrEnum):
    DIGITAL = "DIGITAL"
    SCAN_LIKE = "SCAN_LIKE"


@dataclass(frozen=True, slots=True)
class PageSignals:
    character_count: int
    image_coverage: float
    has_fonts: bool


@dataclass(frozen=True, slots=True)
class ScanDetectionConfig:
    minimum_characters: int = 30
    minimum_image_coverage: float = 0.6


def classify_pdf_page(signals: PageSignals, config: ScanDetectionConfig) -> PageKind:
    scan_like = (
        signals.character_count < config.minimum_characters
        and signals.image_coverage >= config.minimum_image_coverage
        and not signals.has_fonts
    )
    return PageKind.SCAN_LIKE if scan_like else PageKind.DIGITAL
