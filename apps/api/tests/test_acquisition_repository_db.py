import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from casezero_api.repository import AcquisitionRepository, CaseStateError, SourceConflictError
from casezero_evidence import DocumentType, SourceDocument, Visibility
from casezero_ntsb.models import AircraftMetadata, CaseMetadata
from psycopg import AsyncConnection
from pydantic import AnyHttpUrl

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
CUTOFF = datetime(2025, 5, 1, tzinfo=UTC)


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
        await repository.enter_blind(case_id, CUTOFF)
        await repository.enter_blind(case_id, CUTOFF)

        cursor = await connection.execute(
            """
            select c.state, c.evidence_cutoff, count(s.id)
            from public.cases c
            left join public.source_documents s on s.case_id = c.id
            where c.id = %s
            group by c.state, c.evidence_cutoff
            """,
            (case_id,),
        )
        assert await cursor.fetchone() == ("BLIND", CUTOFF, 1)

        changed = document.model_copy(update={"id": uuid4(), "checksum": "b" * 64})
        with pytest.raises(SourceConflictError):
            await repository.add(changed, "bb/" + "b" * 64)


@pytest.mark.db
@pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1")
@pytest.mark.asyncio
async def test_enter_blind_backfills_once_and_preserves_cutoff_after_conflict() -> None:
    case_id = uuid4()
    async with (
        await AsyncConnection.connect(DATABASE_URL) as connection,
        connection.transaction(force_rollback=True),
    ):
        await connection.execute(
            "insert into public.cases (id, ntsb_number, title, state) values (%s, %s, %s, 'BLIND')",
            (case_id, f"TEST-{case_id.hex[:12]}", "Migrated fixture"),
        )
        repository = AcquisitionRepository(connection)

        await repository.enter_blind(case_id, CUTOFF)
        with pytest.raises(CaseStateError):
            await repository.enter_blind(
                case_id, datetime(2025, 5, 2, tzinfo=UTC)
            )

        cutoff = await (
            await connection.execute(
                "select evidence_cutoff from public.cases where id = %s", (case_id,)
            )
        ).fetchone()
        assert cutoff == (CUTOFF,)
