from casezero_ingestion.scan_detection import (
    PageKind,
    PageSignals,
    ScanDetectionConfig,
    classify_pdf_page,
)


def test_digital_page_with_text_and_fonts_is_not_ocr_routed() -> None:
    signals = PageSignals(character_count=500, image_coverage=0.1, has_fonts=True)
    assert classify_pdf_page(signals, ScanDetectionConfig()) is PageKind.DIGITAL


def test_image_dominant_page_without_text_is_scan_like() -> None:
    signals = PageSignals(character_count=0, image_coverage=0.95, has_fonts=False)
    assert classify_pdf_page(signals, ScanDetectionConfig()) is PageKind.SCAN_LIKE


def test_threshold_configuration_changes_classification_deterministically() -> None:
    signals = PageSignals(character_count=20, image_coverage=0.7, has_fonts=False)
    strict = ScanDetectionConfig(minimum_characters=30, minimum_image_coverage=0.6)
    permissive = ScanDetectionConfig(minimum_characters=10, minimum_image_coverage=0.8)
    assert classify_pdf_page(signals, strict) is PageKind.SCAN_LIKE
    assert classify_pdf_page(signals, permissive) is PageKind.DIGITAL
