import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from casezero_api.lock import (
    InvestigationLockError,
    InvestigationLockService,
    PostgresInvestigationLockRepository,
)
from casezero_evidence import AssessmentSnapshot, InvestigationLock
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
CASE_ID = UUID("40000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 9, 1, tzinfo=UTC)


class LockRepository:
    def __init__(self) -> None:
        self.requests: list[tuple[object, ...]] = []

    async def create_lock(
        self,
        case_id: UUID,
        snapshot: AssessmentSnapshot,
        model_versions: dict[str, str],
        prompt_versions: dict[str, str],
        system_version: str,
    ) -> InvestigationLock:
        self.requests.append(
            (case_id, snapshot, model_versions, prompt_versions, system_version)
        )
        return InvestigationLock(
            case_id=case_id,
            assessment_snapshot=snapshot,
            assessment_hash="a" * 64,
            evidence_set_hash="b" * 64,
            hash_algorithm="postgres-jsonb-text-v1",
            model_versions=model_versions,
            prompt_versions=prompt_versions,
            system_version=system_version,
            locked_at=NOW,
        )


@pytest.mark.asyncio
async def test_lock_service_passes_typed_snapshot_and_versions() -> None:
    repository = LockRepository()
    snapshot = AssessmentSnapshot(
        schema_version="phase2-lock-contract-v1",
        assessment_kind="fixture",
        payload={"result": "INSUFFICIENT_EVIDENCE"},
    )

    lock = await InvestigationLockService(repository).create_lock(
        CASE_ID,
        snapshot,
        model_versions={"evidence": "qwen/qwen3-32b"},
        prompt_versions={"evidence": "casezero.evidence.v1"},
        system_version="phase2-test",
    )

    assert lock.case_id == CASE_ID
    assert repository.requests == [
        (
            CASE_ID,
            snapshot,
            {"evidence": "qwen/qwen3-32b"},
            {"evidence": "casezero.evidence.v1"},
            "phase2-test",
        )
    ]


@pytest.mark.asyncio
async def test_lock_service_rejects_empty_version_metadata_before_repository() -> None:
    repository = LockRepository()
    snapshot = AssessmentSnapshot(
        schema_version="phase2-lock-contract-v1",
        assessment_kind="fixture",
        payload={},
    )

    with pytest.raises(ValueError, match="model_versions"):
        await InvestigationLockService(repository).create_lock(
            CASE_ID,
            snapshot,
            model_versions={},
            prompt_versions={"evidence": "casezero.evidence.v1"},
            system_version="phase2-test",
        )

    assert repository.requests == []


@pytest.mark.db
@pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1")
@pytest.mark.asyncio
async def test_database_lock_is_atomic_immutable_snapshot_transition() -> None:
    case_id = uuid4()
    docket_id = uuid4()
    source_id = uuid4()
    processing_run_id = uuid4()
    artifact_id = uuid4()
    unit_id = uuid4()
    model_run_id = uuid4()
    evidence_id = uuid4()
    snapshot = AssessmentSnapshot(
        schema_version="phase2-lock-contract-v1",
        assessment_kind="fixture",
        payload={"result": "INSUFFICIENT_EVIDENCE"},
    )
    async with (
        await AsyncConnection.connect(DATABASE_URL) as connection,
        connection.transaction(force_rollback=True),
    ):
        await connection.execute(
            """
            insert into public.cases (
              id, ntsb_number, title, state, evidence_cutoff
            ) values (%s, %s, 'Lock fixture', 'BLIND', %s)
            """,
            (case_id, f"TEST-{case_id.hex[:12]}", NOW),
        )
        await connection.execute(
            """
            insert into public.docket_items (
              id, case_id, title, source_url, document_type, rights_status,
              processing_disposition, attribution, review_note, reviewed_at
            ) values (%s, %s, 'Factual', %s, 'FACTUAL_REPORT', 'NTSB_AUTHORED',
                      'AI_ALLOWED', 'Source: National Transportation Safety Board',
                      'fixture', %s)
            """,
            (docket_id, case_id, f"https://data.ntsb.gov/{case_id}.pdf", NOW),
        )
        await connection.execute(
            """
            insert into public.source_documents (
              id, case_id, docket_item_id, title, source_url, published_at,
              retrieved_at, document_type, visibility, checksum, storage_path
            ) values (%s, %s, %s, 'Factual', %s, %s, %s, 'FACTUAL_REPORT',
                      'INVESTIGATION_EVIDENCE', %s, %s)
            """,
            (
                source_id,
                case_id,
                docket_id,
                f"https://data.ntsb.gov/{case_id}.pdf",
                NOW,
                NOW,
                "a" * 64,
                f"phase2/{case_id}",
            ),
        )
        await connection.execute(
            """
            insert into public.processing_runs (
              id, source_document_id, source_checksum, processor_name,
              processor_version, configuration_hash, status, started_at, completed_at
            ) values (%s, %s, %s, 'fixture', '1.0.0', %s, 'SUCCEEDED', %s, %s)
            """,
            (processing_run_id, source_id, "a" * 64, "b" * 64, NOW, NOW),
        )
        await connection.execute(
            """
            insert into public.derived_artifacts (
              id, processing_run_id, source_document_id, kind, checksum,
              storage_path, media_type, byte_size, created_at
            ) values (%s, %s, %s, 'DOCUMENT_STRUCTURE', %s, %s,
                      'application/json', 1, %s)
            """,
            (artifact_id, processing_run_id, source_id, "c" * 64, f"phase2/{artifact_id}", NOW),
        )
        await connection.execute(
            """
            insert into public.structural_units (
              id, derived_artifact_id, source_document_id, kind, ordinal,
              content_checksum, locator, payload
            ) values (%s, %s, %s, 'TEXT_BLOCK', 0, %s, %s, %s)
            """,
            (
                unit_id,
                artifact_id,
                source_id,
                "d" * 64,
                Jsonb({"kind": "text", "start": 0, "end": 8}),
                Jsonb({"text": "evidence"}),
            ),
        )
        await connection.execute(
            """
            insert into public.model_runs (
              id, case_id, stage, provider, model, prompt_hash,
              structural_unit_ids, latency_ms, retry_count,
              schema_failure_count, status, created_at
            ) values (%s, %s, 'evidence', 'hyperfusion', 'fixture', %s,
                      %s, 1, 0, 0, 'SUCCEEDED', %s)
            """,
            (model_run_id, case_id, "e" * 64, [unit_id], NOW),
        )
        await connection.execute(
            """
            insert into public.evidence_items (
              id, case_id, source_document_id, structural_unit_id,
              model_run_id, item, review_status, created_at
            ) values (%s, %s, %s, %s, %s, %s, 'NOT_REQUIRED', %s)
            """,
            (
                evidence_id,
                case_id,
                source_id,
                unit_id,
                model_run_id,
                Jsonb({"id": str(evidence_id), "observation": "evidence"}),
                NOW,
            ),
        )
        await connection.execute(
            """
            insert into public.semantic_unit_completions (
              structural_unit_id, model_run_id, evidence_count, completed_at
            ) values (%s, %s, 1, %s)
            """,
            (unit_id, model_run_id, NOW),
        )

        await connection.execute("set local role casezero_blind")
        service = InvestigationLockService(
            PostgresInvestigationLockRepository(connection)
        )
        lock = await service.create_lock(
            case_id,
            snapshot,
            model_versions={"evidence": "fixture"},
            prompt_versions={"evidence": "fixture.v1"},
            system_version="phase2-test",
        )
        with pytest.raises(InvestigationLockError):
            await service.create_lock(
                case_id,
                snapshot,
                model_versions={"evidence": "fixture"},
                prompt_versions={"evidence": "fixture.v1"},
                system_version="phase2-test",
            )
        await connection.execute("reset role")

        state = await (
            await connection.execute("select state from public.cases where id = %s", (case_id,))
        ).fetchone()
        audit_count = await (
            await connection.execute(
                "select count(*) from public.access_audit_events where case_id = %s and reason_code = 'LOCK_CREATED'",
                (case_id,),
            )
        ).fetchone()
        canonical_hash = await (
            await connection.execute(
                """
                select encode(
                  extensions.digest(convert_to(%s::jsonb::text, 'UTF8'), 'sha256'),
                  'hex'
                )
                """,
                (
                    Jsonb(
                        {
                            "payload": {"result": "INSUFFICIENT_EVIDENCE"},
                            "assessment_kind": "fixture",
                            "schema_version": "phase2-lock-contract-v1",
                        }
                    ),
                ),
            )
        ).fetchone()

        assert state == ("LOCKED",)
        assert audit_count == (1,)
        assert lock.assessment_snapshot == snapshot
        assert canonical_hash == (lock.assessment_hash,)
        assert len(lock.assessment_hash) == 64
        assert len(lock.evidence_set_hash) == 64
