import csv
import io
import zipfile
from enum import StrEnum


class MediaDetectionError(ValueError):
    pass


class DetectedMediaType(StrEnum):
    PDF = "PDF"
    PNG = "PNG"
    JPEG = "JPEG"
    CSV = "CSV"
    XLSX = "XLSX"
    HTML = "HTML"
    TEXT = "TEXT"


def detect_media_type(data: bytes, filename: str) -> DetectedMediaType:
    if data.startswith(b"%PDF-"):
        return DetectedMediaType.PDF
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return DetectedMediaType.PNG
    if data.startswith(b"\xff\xd8\xff"):
        return DetectedMediaType.JPEG
    if data.startswith(b"PK\x03\x04") and _is_xlsx(data):
        return DetectedMediaType.XLSX
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MediaDetectionError(f"unsupported binary media: {filename}") from error
    if any(ord(character) < 9 or 13 < ord(character) < 32 for character in text):
        raise MediaDetectionError(f"unsupported binary media: {filename}")
    normalized = text.lstrip().casefold()
    if normalized.startswith(("<!doctype html", "<html")):
        return DetectedMediaType.HTML
    if _is_csv(text):
        return DetectedMediaType.CSV
    if text:
        return DetectedMediaType.TEXT
    raise MediaDetectionError(f"empty or unsupported media: {filename}")


def _is_xlsx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return False
    return {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names)


def _is_csv(text: str) -> bool:
    lines = [line for line in text.splitlines() if line]
    if len(lines) < 2:
        return False
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:10]), delimiters=",;\t|")
    except csv.Error:
        return False
    widths = [len(next(csv.reader([line], dialect))) for line in lines[:10]]
    return widths[0] > 1 and len(set(widths)) == 1
