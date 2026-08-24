from dataclasses import asdict
from uuid import UUID

from casezero_evidence import SourceDocument
from casezero_ntsb.models import CaseMetadata
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb


class CaseStateError(RuntimeError):
    pass


class SourceConflictError(RuntimeError):
    pass


class AcquisitionRepository:
    def __init__(self, connection: AsyncConnection[tuple[object, ...]]) -> None:
        self._connection = connection

    async def upsert_case(self, metadata: CaseMetadata) -> UUID:
        cursor = await self._connection.execute(
            """
            insert into public.cases (ntsb_number, title, event_date, metadata, state)
            values (%s, %s, %s, %s, 'ACQUIRING')
            on conflict (ntsb_number) do update
              set title = excluded.title,
                  event_date = excluded.event_date,
                  metadata = excluded.metadata
            returning id, state
            """,
            (
                metadata.ntsb_number,
                metadata.ntsb_number,
                metadata.event_date,
                Jsonb(
                    {
                        "location": metadata.location,
                        "aircraft": asdict(metadata.aircraft),
                        "status": metadata.status,
                        "has_final_report": metadata.has_final_report,
                        "mkey": metadata.mkey,
                    }
                ),
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("case upsert returned no row")
        case_id, state = row
        if state in {"LOCKED", "REVEALED"}:
            raise CaseStateError(f"cannot reacquire a case in state {state}")
        if not isinstance(case_id, UUID):
            raise TypeError("case upsert returned an invalid id")
        return case_id

    async def add(self, document: SourceDocument, storage_path: str) -> None:
        cursor = await self._connection.execute(
            """
            insert into public.source_documents (
              id, case_id, title, source_url, published_at, evidence_date,
              retrieved_at, document_type, visibility, checksum, storage_path
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (case_id, source_url) do nothing
            returning checksum
            """,
            (
                document.id,
                document.case_id,
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
        if inserted is not None:
            return

        existing_cursor = await self._connection.execute(
            """
            select checksum
            from public.source_documents
            where case_id = %s and source_url = %s
            """,
            (document.case_id, str(document.source_url)),
        )
        existing = await existing_cursor.fetchone()
        if existing is None or existing[0] != document.checksum:
            raise SourceConflictError(
                f"source {document.source_url} changed after its first ingestion"
            )

    async def mark_blind(self, case_id: UUID) -> None:
        cursor = await self._connection.execute(
            """
            update public.cases
            set state = 'BLIND'
            where id = %s and state in ('ACQUIRING', 'BLIND')
            returning id
            """,
            (case_id,),
        )
        if await cursor.fetchone() is None:
            raise CaseStateError("case cannot transition to BLIND")
