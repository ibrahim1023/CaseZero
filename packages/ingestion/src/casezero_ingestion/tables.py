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
from pydantic import JsonValue

from casezero_ingestion.media import DetectedMediaType
from casezero_ingestion.processors import StructuralOutput, StructuralUnitDraft


class TableProcessor:
    name = "polars-table"
    version = "1.0.0"

    def __init__(self, row_group_size: int = 500) -> None:
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
        if source.docket_item.file_type == "xlsx":
            reader = fastexcel.read_excel(source.data)
            result = []
            for sheet_name in reader.sheet_names:
                frame = reader.load_sheet_by_name(sheet_name).to_polars()
                columns = list(frame.columns)
                rows = [["" if value is None else str(value) for value in row] for row in frame.rows()]
                result.append((sheet_name, columns, rows))
            return result
        text = source.data.decode("utf-8-sig")
        parsed = list(csv.reader(io.StringIO(text)))
        if not parsed:
            raise ValueError("table source is empty")
        return [("", parsed[0], parsed[1:])]
