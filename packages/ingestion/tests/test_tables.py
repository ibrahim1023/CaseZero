from datetime import UTC, datetime
from uuid import UUID

from casezero_evidence import (
    DocketItem,
    DocumentType,
    ProcessingSource,
    RightsStatus,
    SourceDocument,
    TableLocator,
    Visibility,
)
from casezero_ingestion.tables import TableProcessor
from pydantic import AnyHttpUrl

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DOC_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")
NOW = datetime(2026, 8, 25, tzinfo=UTC)


def source(data: bytes, file_type: str) -> ProcessingSource:
    url = AnyHttpUrl("https://data.ntsb.gov/data.csv")
    return ProcessingSource(
        docket_item=DocketItem(case_id=CASE_ID, title="Flight data", source_url=url, document_type=DocumentType.FLIGHT_DATA, file_type=file_type, rights_status=RightsStatus.NTSB_AUTHORED, attribution="Source: National Transportation Safety Board", review_note="fixture", reviewed_at=NOW),
        document=SourceDocument(id=DOC_ID, case_id=CASE_ID, title="Flight data", source_url=url, retrieved_at=NOW, document_type=DocumentType.FLIGHT_DATA, visibility=Visibility.INVESTIGATION_EVIDENCE, checksum="a" * 64),
        data=data,
    )


def test_default_table_windows_bound_model_payload_to_fifty_rows() -> None:
    rows = "\n".join(f"{index},{100 + index}" for index in range(120))
    output = TableProcessor().process(
        source(("time,altitude\n" + rows + "\n").encode(), "csv")
    )
    assert [len(unit.payload["rows"]) for unit in output.units] == [50, 50, 20]


def test_csv_processor_preserves_rows_columns_and_raw_values() -> None:
    output = TableProcessor(row_group_size=2).process(source(b"time,altitude\n0,100\n1,120\n2,90\n", "csv"))
    assert len(output.units) == 2
    locator = output.units[0].locator
    assert isinstance(locator, TableLocator)
    assert locator.row == 2 and locator.row_end == 3
    assert locator.columns == ("time", "altitude")
    assert output.units[0].payload["rows"] == [["0", "100"], ["1", "120"]]
