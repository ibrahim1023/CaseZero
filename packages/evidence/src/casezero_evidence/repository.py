from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from casezero_evidence.candidates import ClaimCandidate, EntityCandidate, TimelineCandidate
from casezero_evidence.models import EvidenceItem, SourceDocument
from casezero_evidence.processing import (
    DerivedArtifact,
    DocketItem,
    ProcessingStatus,
    StructuralUnit,
)


class EvidencePersistenceConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProcessableSource:
    docket_item_id: UUID
    source_document_id: UUID
    checksum: str
    storage_path: str
    processing_disposition: str


@dataclass(frozen=True, slots=True)
class StoredSourceRecord:
    document: SourceDocument
    storage_path: str


class EvidenceRepository:
    def __init__(self, connection: AsyncConnection[tuple[object, ...]]) -> None:
        self._connection = connection

    async def get_case_id(self, ntsb_number: str) -> UUID | None:
        cursor = await self._connection.execute(
            "select id from public.cases where ntsb_number = %s", (ntsb_number,)
        )
        row = await cursor.fetchone()
        return row[0] if row is not None and isinstance(row[0], UUID) else None

    async def record_processing_skip(
        self, docket_item_id: UUID, reason: str, created_at: datetime
    ) -> None:
        await self._connection.execute(
            """
            insert into public.processing_skips (
              docket_item_id, status, reason, created_at
            ) values (%s, 'SKIPPED_RIGHTS', %s, %s)
            on conflict (docket_item_id, status) do update set
              reason = excluded.reason, created_at = excluded.created_at
            """,
            (docket_item_id, reason, created_at),
        )

    async def get_source_document(
        self, case_id: UUID, source_url: str
    ) -> StoredSourceRecord | None:
        cursor = await self._connection.execute(
            """
            select id, case_id, title, source_url, published_at, evidence_date,
                   retrieved_at, document_type, visibility, checksum, storage_path
            from public.source_documents
            where case_id = %s and source_url = %s
            """,
            (case_id, source_url),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        document = SourceDocument.model_validate(
            {
                "id": row[0],
                "case_id": row[1],
                "title": row[2],
                "source_url": row[3],
                "published_at": row[4],
                "evidence_date": row[5],
                "retrieved_at": row[6],
                "document_type": row[7],
                "visibility": row[8],
                "checksum": row[9],
            },
            strict=False,
        )
        return StoredSourceRecord(document=document, storage_path=str(row[10]))

    async def upsert_docket_item(self, item: DocketItem) -> UUID:
        if item.processing_disposition is None:
            raise TypeError("docket item disposition must be resolved")
        cursor = await self._connection.execute(
            """
            insert into public.docket_items (
              id, case_id, title, source_url, document_type, file_type, page_count,
              published_at, rights_status, processing_disposition, attribution,
              review_note, reviewed_at, expected_checksum
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (case_id, source_url) do update set
              title = excluded.title,
              document_type = excluded.document_type,
              file_type = excluded.file_type,
              page_count = excluded.page_count,
              published_at = excluded.published_at,
              rights_status = excluded.rights_status,
              processing_disposition = excluded.processing_disposition,
              attribution = excluded.attribution,
              review_note = excluded.review_note,
              reviewed_at = excluded.reviewed_at,
              expected_checksum = excluded.expected_checksum
            returning id
            """,
            (
                item.id,
                item.case_id,
                item.title,
                str(item.source_url),
                item.document_type.value if item.document_type else None,
                item.file_type,
                item.page_count,
                item.published_at,
                item.rights_status.value,
                item.processing_disposition.value,
                item.attribution,
                item.review_note,
                item.reviewed_at,
                item.expected_checksum,
            ),
        )
        row = await cursor.fetchone()
        if row is None or not isinstance(row[0], UUID):
            raise RuntimeError("docket item upsert returned no id")
        return row[0]

    async def link_source_document(
        self,
        document: SourceDocument,
        docket_item_id: UUID,
        storage_path: str,
        byte_size: int,
    ) -> None:
        await self._connection.execute(
            """
            insert into public.source_blobs (checksum, storage_path, byte_size)
            values (%s, %s, %s)
            on conflict (checksum) do update set byte_size = excluded.byte_size
            where public.source_blobs.storage_path = excluded.storage_path
              and public.source_blobs.byte_size = 0
            """,
            (document.checksum, storage_path, byte_size),
        )
        blob_cursor = await self._connection.execute(
            "select storage_path, byte_size from public.source_blobs where checksum = %s",
            (document.checksum,),
        )
        blob = await blob_cursor.fetchone()
        if blob != (storage_path, byte_size):
            raise EvidencePersistenceConflict("source blob metadata conflicts with checksum")

        cursor = await self._connection.execute(
            """
            insert into public.source_documents (
              id, case_id, docket_item_id, blob_checksum, title, source_url,
              published_at, evidence_date, retrieved_at, document_type,
              visibility, checksum, storage_path
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (case_id, source_url) do update set
              docket_item_id = excluded.docket_item_id,
              blob_checksum = excluded.blob_checksum
            where public.source_documents.checksum = excluded.checksum
            returning checksum
            """,
            (
                document.id,
                document.case_id,
                docket_item_id,
                document.checksum,
                document.title,
                str(document.source_url),
                document.published_at,
                document.evidence_date,
                document.retrieved_at,
                document.document_type.value,
                document.visibility.value,
                document.checksum,
                storage_path,
            ),
        )
        inserted = await cursor.fetchone()
        if inserted is None:
            existing_cursor = await self._connection.execute(
                "select checksum from public.source_documents where case_id = %s and source_url = %s",
                (document.case_id, str(document.source_url)),
            )
            existing = await existing_cursor.fetchone()
            if existing is None or existing[0] != document.checksum:
                raise EvidencePersistenceConflict("source document checksum changed")

    async def get_processable_sources(self, case_id: UUID) -> tuple[ProcessableSource, ...]:
        cursor = await self._connection.execute(
            """
            select d.id, s.id, s.checksum, s.storage_path, d.processing_disposition
            from public.docket_items d
            join public.source_documents s on s.docket_item_id = d.id
            where d.case_id = %s
              and d.processing_disposition in ('AI_ALLOWED', 'LOCAL_ONLY')
              and s.visibility = 'INVESTIGATION_EVIDENCE'
            order by d.id
            """,
            (case_id,),
        )
        rows = await cursor.fetchall()
        return tuple(
            ProcessableSource(
                docket_item_id=row[0],
                source_document_id=row[1],
                checksum=str(row[2]),
                storage_path=str(row[3]),
                processing_disposition=str(row[4]),
            )
            for row in rows
            if isinstance(row[0], UUID) and isinstance(row[1], UUID)
        )

    async def start_processing_run(
        self,
        *,
        source_document_id: UUID,
        source_checksum: str,
        processor_name: str,
        processor_version: str,
        configuration_hash: str,
        started_at: datetime,
    ) -> UUID:
        cursor = await self._connection.execute(
            """
            insert into public.processing_runs (
              source_document_id, source_checksum, processor_name,
              processor_version, configuration_hash, status, started_at
            ) values (%s, %s, %s, %s, %s, 'RUNNING', %s)
            on conflict (source_checksum, processor_name, processor_version, configuration_hash)
            do update set status = 'RUNNING', error_type = null, error_message = null,
              retryable = false, attempt_count = public.processing_runs.attempt_count + 1,
              started_at = excluded.started_at, completed_at = null
            returning id
            """,
            (
                source_document_id,
                source_checksum,
                processor_name,
                processor_version,
                configuration_hash,
                started_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None or not isinstance(row[0], UUID):
            raise RuntimeError("processing run insert returned no id")
        return row[0]

    async def find_successful_run(
        self, source_checksum: str, processor_name: str, processor_version: str, configuration_hash: str
    ) -> UUID | None:
        cursor = await self._connection.execute(
            """
            select id from public.processing_runs
            where source_checksum = %s and processor_name = %s
              and processor_version = %s and configuration_hash = %s
              and status = 'SUCCEEDED'
            """,
            (source_checksum, processor_name, processor_version, configuration_hash),
        )
        row = await cursor.fetchone()
        return row[0] if row is not None and isinstance(row[0], UUID) else None

    async def complete_processing_run(self, run_id: UUID, completed_at: datetime) -> None:
        await self._set_run_result(run_id, ProcessingStatus.SUCCEEDED, completed_at, None, None, False)

    async def fail_processing_run(
        self,
        run_id: UUID,
        completed_at: datetime,
        error_type: str,
        error_message: str,
        retryable: bool,
    ) -> None:
        await self._set_run_result(
            run_id,
            ProcessingStatus.FAILED,
            completed_at,
            error_type,
            error_message,
            retryable,
        )

    async def mark_processing_run_unsupported(
        self,
        run_id: UUID,
        completed_at: datetime,
        error_type: str,
        error_message: str,
    ) -> None:
        await self._set_run_result(
            run_id,
            ProcessingStatus.UNSUPPORTED,
            completed_at,
            error_type,
            error_message,
            False,
        )

    async def _set_run_result(
        self,
        run_id: UUID,
        status: ProcessingStatus,
        completed_at: datetime,
        error_type: str | None,
        error_message: str | None,
        retryable: bool,
    ) -> None:
        cursor = await self._connection.execute(
            """
            update public.processing_runs
            set status = %s, completed_at = %s, error_type = %s,
                error_message = %s, retryable = %s
            where id = %s returning id
            """,
            (status.value, completed_at, error_type, error_message, retryable, run_id),
        )
        if await cursor.fetchone() is None:
            raise LookupError(f"processing run {run_id} does not exist")

    async def add_artifact_with_units(
        self, artifact: DerivedArtifact, units: tuple[StructuralUnit, ...]
    ) -> None:
        async with self._connection.transaction():
            await self.add_derived_artifact(artifact)
            await self.add_structural_units(units)

    async def add_derived_artifact(self, artifact: DerivedArtifact) -> None:
        await self._connection.execute(
            """
            insert into public.derived_artifacts (
              id, processing_run_id, source_document_id, kind, checksum,
              storage_path, media_type, byte_size, tool_metadata, created_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (id) do nothing
            """,
            (
                artifact.id,
                artifact.processing_run_id,
                artifact.source_document_id,
                artifact.kind.value,
                artifact.checksum,
                artifact.storage_path,
                artifact.media_type,
                artifact.byte_size,
                Jsonb(artifact.tool_metadata),
                artifact.created_at,
            ),
        )

    async def add_structural_units(self, units: tuple[StructuralUnit, ...]) -> None:
        for unit in units:
            await self._connection.execute(
                """
                insert into public.structural_units (
                  id, derived_artifact_id, source_document_id, kind, ordinal,
                  content_checksum, locator, payload
                ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (id) do nothing
                """,
                (
                    unit.id,
                    unit.derived_artifact_id,
                    unit.source_document_id,
                    unit.kind.value,
                    unit.ordinal,
                    unit.content_checksum,
                    Jsonb(unit.locator.model_dump(mode="json")),
                    Jsonb(unit.payload),
                ),
            )

    async def get_structural_units_for_run(
        self, run_id: UUID
    ) -> tuple[StructuralUnit, ...]:
        cursor = await self._connection.execute(
            """
            select u.id, u.derived_artifact_id, u.source_document_id, u.kind,
                   u.ordinal, u.content_checksum, u.locator, u.payload
            from public.structural_units u
            join public.derived_artifacts a on a.id = u.derived_artifact_id
            where a.processing_run_id = %s
            order by u.ordinal
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
        return tuple(
            StructuralUnit.model_validate(
                {
                    "id": row[0],
                    "derived_artifact_id": row[1],
                    "source_document_id": row[2],
                    "kind": row[3],
                    "ordinal": row[4],
                    "content_checksum": row[5],
                    "locator": row[6],
                    "payload": row[7],
                },
                strict=False,
            )
            for row in rows
        )

    async def add_evidence_items(
        self, items: tuple[EvidenceItem, ...], created_at: datetime
    ) -> None:
        for item in items:
            if item.structural_unit_id is None:
                raise ValueError("persisted EvidenceItem requires structural_unit_id")
            await self._connection.execute(
                """
                insert into public.evidence_items (
                  id, case_id, source_document_id, structural_unit_id,
                  model_run_id, item, review_status, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (id) do nothing
                """,
                (
                    item.id,
                    item.case_id,
                    item.source_document_id,
                    item.structural_unit_id,
                    item.model_run_id,
                    Jsonb(item.model_dump(mode="json")),
                    item.review_status.value,
                    created_at,
                ),
            )

    async def get_evidence_items_for_unit(
        self, structural_unit_id: UUID
    ) -> tuple[EvidenceItem, ...]:
        cursor = await self._connection.execute(
            """
            select item from public.evidence_items
            where structural_unit_id = %s
            order by created_at, id
            """,
            (structural_unit_id,),
        )
        rows = await cursor.fetchall()
        return tuple(EvidenceItem.model_validate(row[0], strict=False) for row in rows)

    async def record_model_run(
        self,
        *,
        run_id: UUID,
        case_id: UUID,
        parent_run_id: UUID | None,
        stage: str,
        provider: str,
        model: str,
        prompt_hash: str,
        structural_unit_ids: tuple[UUID, ...],
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
        retry_count: int,
        schema_failure_count: int,
        status: str,
        created_at: datetime,
    ) -> None:
        await self._connection.execute(
            """
            insert into public.model_runs (
              id, case_id, parent_run_id, stage, provider, model, prompt_hash,
              structural_unit_ids, input_tokens, output_tokens, latency_ms,
              retry_count, schema_failure_count, status, created_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (id) do nothing
            """,
            (
                run_id,
                case_id,
                parent_run_id,
                stage,
                provider,
                model,
                prompt_hash,
                list(structural_unit_ids),
                input_tokens,
                output_tokens,
                latency_ms,
                retry_count,
                schema_failure_count,
                status,
                created_at,
            ),
        )

    async def has_successful_model_run(
        self,
        case_id: UUID,
        stage: str,
        structural_unit_ids: tuple[UUID, ...],
    ) -> bool:
        cursor = await self._connection.execute(
            """
            select 1 from public.model_runs
            where case_id = %s and stage = %s and structural_unit_ids = %s
              and status = 'SUCCEEDED'
            limit 1
            """,
            (case_id, stage, list(structural_unit_ids)),
        )
        return await cursor.fetchone() is not None

    async def has_completed_semantic_unit(self, structural_unit_id: UUID) -> bool:
        cursor = await self._connection.execute(
            "select 1 from public.semantic_unit_completions where structural_unit_id = %s",
            (structural_unit_id,),
        )
        return await cursor.fetchone() is not None

    async def complete_semantic_unit(
        self, structural_unit_id: UUID, evidence_count: int, completed_at: datetime
    ) -> None:
        await self._connection.execute(
            """
            insert into public.semantic_unit_completions (
              structural_unit_id, model_run_id, evidence_count, completed_at
            )
            select %s, id, %s, %s
            from public.model_runs
            where stage = 'evidence' and structural_unit_ids = %s and status = 'SUCCEEDED'
            order by created_at desc
            limit 1
            on conflict (structural_unit_id) do update set
              model_run_id = excluded.model_run_id,
              evidence_count = excluded.evidence_count,
              completed_at = excluded.completed_at
            """,
            (structural_unit_id, evidence_count, completed_at, [structural_unit_id]),
        )

    async def persist_semantic_result(
        self,
        structural_unit_id: UUID,
        items: tuple[EvidenceItem, ...],
        completed_at: datetime,
    ) -> None:
        async with self._connection.transaction():
            await self.add_evidence_items(items, completed_at)
            await self.complete_semantic_unit(
                structural_unit_id, len(items), completed_at
            )

    async def has_persisted_candidate_run(
        self, case_id: UUID, structural_unit_ids: tuple[UUID, ...]
    ) -> bool:
        cursor = await self._connection.execute(
            """
            select 1
            from public.model_runs as run
            where run.case_id = %s
              and run.stage = 'candidates'
              and run.structural_unit_ids = %s
              and run.status = 'SUCCEEDED'
              and (
                exists (select 1 from public.claim_candidates where model_run_id = run.id)
                or exists (select 1 from public.entity_candidates where model_run_id = run.id)
                or exists (select 1 from public.timeline_candidates where model_run_id = run.id)
              )
            limit 1
            """,
            (case_id, list(structural_unit_ids)),
        )
        return await cursor.fetchone() is not None

    async def get_model_usage(self, run_ids: tuple[UUID, ...]) -> dict[str, int]:
        if not run_ids:
            return {}
        cursor = await self._connection.execute(
            """
            select provider || '/' || model, count(*)
            from public.model_runs
            where id = any(%s) and status = 'SUCCEEDED'
            group by provider, model
            order by provider, model
            """,
            (list(dict.fromkeys(run_ids)),),
        )
        return {str(row[0]): int(str(row[1])) for row in await cursor.fetchall()}

    async def persist_candidate_batch(
        self, candidates: tuple[ClaimCandidate | EntityCandidate | TimelineCandidate, ...]
    ) -> None:
        async with self._connection.transaction():
            await self.add_candidates(candidates)

    async def add_candidates(
        self, candidates: tuple[ClaimCandidate | EntityCandidate | TimelineCandidate, ...]
    ) -> None:
        for candidate in candidates:
            if isinstance(candidate, ClaimCandidate):
                table = "claim_candidates"
                evidence_ids = candidate.supporting_evidence_ids + candidate.contradicting_evidence_ids
            elif isinstance(candidate, EntityCandidate):
                table = "entity_candidates"
                evidence_ids = candidate.evidence_ids
            else:
                table = "timeline_candidates"
                evidence_ids = candidate.evidence_ids
            unique_evidence_ids = tuple(dict.fromkeys(evidence_ids))
            evidence_cursor = await self._connection.execute(
                "select id from public.evidence_items where id = any(%s)",
                (list(unique_evidence_ids),),
            )
            persisted_ids = {row[0] for row in await evidence_cursor.fetchall()}
            if persisted_ids != set(unique_evidence_ids):
                raise ValueError("candidate references evidence that is not persisted")
            await self._connection.execute(
                f"""
                insert into public.{table} (
                  id, case_id, model_run_id, evidence_ids, candidate, created_at
                ) values (%s, %s, %s, %s, %s, %s)
                on conflict (id) do nothing
                """,
                (
                    candidate.id,
                    candidate.case_id,
                    candidate.model_run_id,
                    list(unique_evidence_ids),
                    Jsonb(candidate.model_dump(mode="json")),
                    candidate.created_at,
                ),
            )
