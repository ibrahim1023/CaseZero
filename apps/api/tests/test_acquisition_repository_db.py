import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from casezero_api.repository import AcquisitionRepository, SourceConflictError
from casezero_evidence import DocumentType, SourceDocument, Visibility
from casezero_ntsb.models import AircraftMetadata, CaseMetadata
from psycopg import AsyncConnection
from pydantic import AnyHttpUrl

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)


@pytest.mark.db
@pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1")
@pytest.mark.asyncio
async def test_repository_persists_idempotent_sources_and_marks_case_blind() -> None:
    ntsb_number = f"TEST-{uuid4().hex[:12]}"
    metadata = CaseMetadata(
        ntsb_number=ntsb_number,
        event_date=datetime(2025, 4, 30, tzinfo=UTC),
        location="Bethany, OK",
        aircraft=AircraftMetadata(make="BELL", model="505"),
        status="Completed",
        has_final_report=True,
    )

    async with (
        await AsyncConnection.connect(DATABASE_URL) as connection,
        connection.transaction(force_rollback=True),
    ):
        repository = AcquisitionRepository(connection)
        case_id = await repository.upsert_case(metadata)
        document = SourceDocument(
            case_id=case_id,
            title="Aircraft factual report",
            source_url=AnyHttpUrl("https://data.ntsb.gov/factual.pdf"),
            retrieved_at=datetime(2026, 8, 24, tzinfo=UTC),
            document_type=DocumentType.FACTUAL_REPORT,
            visibility=Visibility.INVESTIGATION_EVIDENCE,
            checksum="a" * 64,
        )

        await repository.add(document, "aa/" + "a" * 64)
        await repository.add(document, "aa/" + "a" * 64)
        await repository.mark_blind(case_id)

        cursor = await connection.execute(
            """
            select c.state, count(s.id)
            from public.cases c
            left join public.source_documents s on s.case_id = c.id
            where c.id = %s
            group by c.state
            """,
            (case_id,),
        )
        assert await cursor.fetchone() == ("BLIND", 1)

        changed = document.model_copy(update={"id": uuid4(), "checksum": "b" * 64})
        with pytest.raises(SourceConflictError):
            await repository.add(changed, "bb/" + "b" * 64)
