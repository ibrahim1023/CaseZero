import io
import zipfile

import pytest
from casezero_ingestion.media import DetectedMediaType, MediaDetectionError, detect_media_type


def xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("xl/workbook.xml", "<workbook />")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("data", "filename", "expected"),
    [
        (b"%PDF-1.7\n", "wrong.txt", DetectedMediaType.PDF),
        (b"\x89PNG\r\n\x1a\n", "image.bin", DetectedMediaType.PNG),
        (b"\xff\xd8\xff\xe0", "image.bin", DetectedMediaType.JPEG),
        (b"<html><body>evidence</body></html>", "page.txt", DetectedMediaType.HTML),
        (b"time,altitude\n0,100\n1,120\n", "flight.csv", DetectedMediaType.CSV),
        (b"plain narrative evidence", "report.txt", DetectedMediaType.TEXT),
    ],
)
def test_media_detection_uses_bytes_before_filename(
    data: bytes, filename: str, expected: DetectedMediaType
) -> None:
    assert detect_media_type(data, filename) is expected


def test_media_detection_recognizes_xlsx_zip_structure() -> None:
    assert detect_media_type(xlsx_bytes(), "workbook.zip") is DetectedMediaType.XLSX


def test_media_detection_rejects_unknown_binary() -> None:
    with pytest.raises(MediaDetectionError):
        detect_media_type(b"\x00\x01\x02\x03", "unknown.bin")
