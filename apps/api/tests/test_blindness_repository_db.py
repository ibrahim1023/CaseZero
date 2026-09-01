import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg import AsyncConnection

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
CUTOFF = datetime(2025, 5, 1, tzinfo=UTC)


@pytest.mark.db
@pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1")
@pytest.mark.asyncio
async def test_runtime_roles_enforce_visibility_cutoff_and_disposition() -> None:
    case_id = uuid4()
    async with (
        await AsyncConnection.connect(DATABASE_URL) as connection,
        connection.transaction(force_rollback=True),
    ):
        await connection.execute(
            """
            insert into public.cases (
              id, ntsb_number, title, state, evidence_cutoff
            ) values (%s, %s, 'Phase 2 fixture', 'BLIND', %s)
            """,
            (case_id, f"TEST-{case_id.hex[:12]}", CUTOFF),
        )
        rows = (
            ("Eligible factual", "FACTUAL_REPORT", "AI_ALLOWED", CUTOFF, "INVESTIGATION_EVIDENCE", "a"),
            ("Official final", "FINAL_REPORT", "AI_ALLOWED", CUTOFF, "FINAL_FINDING", "b"),
            ("Post cutoff factual", "FACTUAL_REPORT", "AI_ALLOWED", datetime(2025, 5, 2, tzinfo=UTC), "INVESTIGATION_EVIDENCE", "c"),
            ("Unknown date factual", "FACTUAL_REPORT", "AI_ALLOWED", None, "INVESTIGATION_EVIDENCE", "d"),
            ("Link only factual", "FACTUAL_REPORT", "LINK_ONLY", CUTOFF, "INVESTIGATION_EVIDENCE", "e"),
            ("Final Report", "FACTUAL_REPORT", "AI_ALLOWED", CUTOFF, "INVESTIGATION_EVIDENCE", "f"),
        )
        for index, (title, document_type, disposition, published_at, visibility, checksum) in enumerate(rows):
            docket_id = uuid4()
            source_id = uuid4()
            source_url = f"https://data.ntsb.gov/phase2-{case_id}-{index}.pdf"
            await connection.execute(
                """
                insert into public.docket_items (
                  id, case_id, title, source_url, document_type, rights_status,
                  processing_disposition, attribution, review_note, reviewed_at
                ) values (%s, %s, %s, %s, %s, 'NTSB_AUTHORED', %s,
                          'Source: National Transportation Safety Board',
                          'Phase 2 fixture review', %s)
                """,
                (docket_id, case_id, title, source_url, document_type, disposition, CUTOFF),
            )
            await connection.execute(
                """
                insert into public.source_documents (
                  id, case_id, docket_item_id, title, source_url, published_at,
                  retrieved_at, document_type, visibility, checksum, storage_path
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_id,
                    case_id,
                    docket_id,
                    title,
                    source_url,
                    published_at,
                    CUTOFF,
                    document_type,
                    visibility,
                    checksum * 64,
                    f"phase2/{checksum * 64}",
                ),
            )

        await connection.execute("set local role casezero_blind")
        blind_count = await (
            await connection.execute(
                "select count(*) from public.source_documents where case_id = %s",
                (case_id,),
            )
        ).fetchone()
        await connection.execute("reset role")

        await connection.execute("set local role casezero_processor")
        processor_count = await (
            await connection.execute(
                "select count(*) from public.source_documents where case_id = %s",
                (case_id,),
            )
        ).fetchone()
        await connection.execute("reset role")

        await connection.execute("set local role casezero_eval")
        evaluation_count = await (
            await connection.execute(
                "select count(*) from public.source_documents where case_id = %s",
                (case_id,),
            )
        ).fetchone()
        await connection.execute("reset role")

        assert blind_count == (1,)
        assert processor_count == (1,)
        assert evaluation_count == (0,)
