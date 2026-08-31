import csv
import hashlib
import io
import json
from typing import Any, cast

import fastexcel
from casezero_evidence import (
    DerivedArtifactKind,
    DocumentType,
    ProcessingSource,
    StructuralUnitKind,
    TableLocator,
)
from casezero_evidence.locator import LocatorResolutionError, ResolvedRegion
from pydantic import JsonValue

from casezero_ingestion.media import DetectedMediaType
from casezero_ingestion.processors import StructuralOutput, StructuralUnitDraft


class TableLocatorAdapter:
    def supports(self, locator: object) -> bool:
        return isinstance(locator, TableLocator)

    def resolve(self, source: bytes, locator: object) -> ResolvedRegion:
        if not isinstance(locator, TableLocator):
            raise LocatorResolutionError("table adapter requires TableLocator")
        file_type = "xlsx" if source.startswith(b"PK\x03\x04") else "csv"
        sheets = _read_table(source, file_type)
        sheet = next((value for value in sheets if value[0] == (locator.sheet or "")), None)
        if sheet is None:
            raise LocatorResolutionError("table sheet does not exist")
        _, columns, rows = sheet
        try:
            indexes = [columns.index(column) for column in locator.columns]
        except ValueError as error:
            raise LocatorResolutionError("table locator column does not exist") from error
        start = locator.row - 2
        end = (locator.row_end or locator.row) - 1
        if start < 0 or end > len(rows):
            raise LocatorResolutionError("table locator exceeds source rows")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(locator.columns)
        writer.writerows([[row[index] for index in indexes] for row in rows[start:end]])
        return ResolvedRegion(
            media_type="text/csv; charset=utf-8",
            content=output.getvalue().encode(),
        )


class TableProcessor:
    name = "polars-table"
    version = "1.0.0"

    def __init__(self, row_group_size: int = 50) -> None:
        self._row_group_size = row_group_size

    def supports(self, media_type: DetectedMediaType, document_type: DocumentType) -> bool:
        return media_type in {DetectedMediaType.CSV, DetectedMediaType.XLSX}

    def configuration(self) -> dict[str, JsonValue]:
        return {"row_group_size": self._row_group_size}

    def process(self, source: ProcessingSource) -> StructuralOutput:
        sheets = self._read(source)
        units: list[StructuralUnitDraft] = []
        profiles: dict[str, Any] = {}
        for sheet_name, columns, rows in sheets:
            profiles[sheet_name] = {"columns": columns, "row_count": len(rows)}
            for offset in range(0, len(rows), self._row_group_size):
                group = rows[offset : offset + self._row_group_size]
                payload: dict[str, JsonValue] = {
                    "columns": cast(list[JsonValue], columns),
                    "rows": cast(list[JsonValue], group),
                    "sheet": sheet_name,
                }
                encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                units.append(
                    StructuralUnitDraft(
                        kind=StructuralUnitKind.TABLE_ROW_GROUP,
                        ordinal=len(units),
                        content_checksum=hashlib.sha256(encoded).hexdigest(),
                        locator=TableLocator(
                            row=offset + 2,
                            row_end=offset + len(group) + 1,
                            columns=tuple(columns),
                            sheet=sheet_name,
                        ),
                        payload=payload,
                    )
                )
        artifact = (json.dumps(profiles, sort_keys=True, separators=(",", ":")) + "\n").encode()
        return StructuralOutput(
            artifact_kind=DerivedArtifactKind.TABLE_PROFILE,
            artifact_bytes=artifact,
            media_type="application/json",
            units=tuple(units),
            tool_metadata={"processor": self.name, "version": self.version},
        )

    @staticmethod
    def _read(source: ProcessingSource) -> list[tuple[str, list[str], list[list[str]]]]:
        return _read_table(source.data, source.docket_item.file_type or "csv")


def _read_table(data: bytes, file_type: str) -> list[tuple[str, list[str], list[list[str]]]]:
    if file_type == "xlsx":
        reader = fastexcel.read_excel(data)
        result = []
        for sheet_name in reader.sheet_names:
            frame = reader.load_sheet_by_name(sheet_name).to_polars()
            columns = list(frame.columns)
            rows = [["" if value is None else str(value) for value in row] for row in frame.rows()]
            result.append((sheet_name, columns, rows))
        return result
    text = data.decode("utf-8-sig")
    parsed = list(csv.reader(io.StringIO(text)))
    if not parsed:
        raise ValueError("table source is empty")
    return [("", parsed[0], parsed[1:])]
