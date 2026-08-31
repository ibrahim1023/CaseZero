import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from casezero_evidence import (
    ClaimCandidate,
    ClaimStatus,
    DerivedArtifact,
    DerivedArtifactKind,
    DocketItem,
    DocumentType,
    EvidenceItem,
    EvidenceType,
    ExtractionMethod,
    PdfLocator,
    RightsStatus,
    SourceDocument,
    StructuralUnit,
    StructuralUnitKind,
    Visibility,
)
from casezero_evidence.repository import EvidenceRepository
from psycopg import AsyncConnection
from pydantic import AnyHttpUrl

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
NOW = datetime(2026, 8, 25, tzinfo=UTC)


@pytest.mark.db
@pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1")
@pytest.mark.asyncio
async def test_repository_persists_idempotent_processing_graph() -> None:
    case_id = uuid4()
    document_id = uuid4()
    model_run_id = uuid4()
    item = DocketItem(
        case_id=case_id,
        title="NTSB examination report",
        source_url=AnyHttpUrl("https://data.ntsb.gov/report.pdf"),
        document_type=DocumentType.EXAMINATION_REPORT,
        rights_status=RightsStatus.NTSB_AUTHORED,
        attribution="Source: National Transportation Safety Board",
        review_note="NTSB-authored report",
        reviewed_at=NOW,
        expected_checksum="a" * 64,
    )
    document = SourceDocument(
        id=document_id,
        case_id=case_id,
        title=item.title,
        source_url=item.source_url,
        retrieved_at=NOW,
        document_type=DocumentType.EXAMINATION_REPORT,
        visibility=Visibility.INVESTIGATION_EVIDENCE,
        checksum="a" * 64,
    )

    async with (
        await AsyncConnection.connect(DATABASE_URL) as connection,
        connection.transaction(force_rollback=True),
    ):
        await connection.execute(
            "insert into public.cases (id, ntsb_number, title, state) values (%s, %s, %s, 'BLIND')",
            (case_id, f"TEST-{case_id.hex[:12]}", "Fixture"),
        )
        repository = EvidenceRepository(connection)
        docket_item_id = await repository.upsert_docket_item(item)
        await repository.link_source_document(document, docket_item_id, "aa/" + "a" * 64, 12)
        second_document = document.model_copy(
            update={
                "id": uuid4(),
                "source_url": AnyHttpUrl("https://data.ntsb.gov/report-copy.pdf"),
            }
        )
        second_item = item.model_copy(
            update={"id": uuid4(), "source_url": second_document.source_url}
        )
        second_item_id = await repository.upsert_docket_item(second_item)
        await repository.link_source_document(
            second_document, second_item_id, "aa/" + "a" * 64, 12
        )

        run_id = await repository.start_processing_run(
            source_document_id=document.id,
            source_checksum=document.checksum,
            processor_name="fixture",
            processor_version="1.0.0",
            configuration_hash="b" * 64,
            started_at=NOW,
        )
        await repository.complete_processing_run(run_id, NOW)
        assert (
            await repository.find_successful_run(
                document.checksum, "fixture", "1.0.0", "b" * 64
            )
            == run_id
        )

        artifact = DerivedArtifact(
            processing_run_id=run_id,
            source_document_id=document.id,
            kind=DerivedArtifactKind.DOCUMENT_STRUCTURE,
            checksum="c" * 64,
            storage_path="cc/" + "c" * 64,
            media_type="application/json",
            byte_size=10,
            created_at=NOW,
        )
        await repository.add_derived_artifact(artifact)
        unit = StructuralUnit(
            derived_artifact_id=artifact.id,
            source_document_id=document.id,
            kind=StructuralUnitKind.TEXT_BLOCK,
            ordinal=0,
            content_checksum="d" * 64,
            locator=PdfLocator(page=1, reading_order=0),
            payload={"text": "Visible source text"},
        )
        await repository.add_structural_units((unit,))
        await connection.execute(
            """
            insert into public.model_runs (
              id, case_id, stage, provider, model, prompt_hash, structural_unit_ids,
              latency_ms, retry_count, schema_failure_count, status, created_at
            ) values (%s, %s, 'evidence', 'test', 'test', %s, %s, 1, 0, 0, 'SUCCEEDED', %s)
            """,
            (model_run_id, case_id, "e" * 64, [unit.id], NOW),
        )
        evidence = EvidenceItem(
            case_id=case_id,
            source_document_id=document.id,
            structural_unit_id=unit.id,
            model_run_id=model_run_id,
            type=EvidenceType.TEXT,
            observation="Visible source text",
            source_locator=unit.locator,
            extraction_method=ExtractionMethod.AI,
            confidence=0.9,
        )
        assert not await repository.has_completed_semantic_unit(unit.id, "e" * 64)
        await repository.persist_semantic_result(unit.id, (evidence,), "e" * 64, NOW)
        assert await repository.has_completed_semantic_unit(unit.id, "e" * 64)
        assert not await repository.has_completed_semantic_unit(unit.id, "f" * 64)
        candidate = ClaimCandidate(
            case_id=case_id,
            text="Visible source text was reported.",
            status=ClaimStatus.OBSERVED,
            supporting_evidence_ids=(evidence.id,),
            confidence=0.9,
            model_run_id=model_run_id,
            created_at=NOW,
        )
        await repository.persist_candidate_batch((candidate,))

        cursor = await connection.execute(
            """
            select
              (select count(*) from public.source_blobs where checksum = %s),
              (select count(*) from public.source_documents where blob_checksum = %s),
              (select count(*) from public.structural_units where id = %s),
              (select count(*) from public.evidence_items where id = %s),
              (select count(*) from public.claim_candidates where id = %s)
            """,
            ("a" * 64, "a" * 64, unit.id, evidence.id, candidate.id),
        )
        assert await cursor.fetchone() == (1, 2, 1, 1, 1)
