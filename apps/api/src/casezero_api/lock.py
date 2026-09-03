from typing import Protocol
from uuid import UUID

from casezero_evidence import AssessmentSnapshot, InvestigationLock
from psycopg import AsyncConnection
from psycopg.errors import DatabaseError
from psycopg.types.json import Jsonb


class InvestigationLockError(RuntimeError):
    pass


class InvestigationLockWriter(Protocol):
    async def create_lock(
        self,
        case_id: UUID,
        snapshot: AssessmentSnapshot,
        model_versions: dict[str, str],
        prompt_versions: dict[str, str],
        system_version: str,
    ) -> InvestigationLock: ...


class InvestigationLockService:
    def __init__(self, repository: InvestigationLockWriter) -> None:
        self._repository = repository

    async def create_lock(
        self,
        case_id: UUID,
        snapshot: AssessmentSnapshot,
        model_versions: dict[str, str],
        prompt_versions: dict[str, str],
        system_version: str,
    ) -> InvestigationLock:
        _validate_versions("model_versions", model_versions)
        _validate_versions("prompt_versions", prompt_versions)
        if not system_version:
            raise ValueError("system_version must not be empty")
        return await self._repository.create_lock(
            case_id,
            snapshot,
            model_versions,
            prompt_versions,
            system_version,
        )


class PostgresInvestigationLockRepository:
    def __init__(self, connection: AsyncConnection[tuple[object, ...]]) -> None:
        self._connection = connection

    async def create_lock(
        self,
        case_id: UUID,
        snapshot: AssessmentSnapshot,
        model_versions: dict[str, str],
        prompt_versions: dict[str, str],
        system_version: str,
    ) -> InvestigationLock:
        try:
            async with self._connection.transaction():
                cursor = await self._connection.execute(
                    """
                    select case_id, assessment_snapshot, assessment_hash,
                           evidence_set_hash, hash_algorithm, model_versions,
                           prompt_versions, system_version, locked_at
                    from public.lock_investigation(%s, %s, %s, %s, %s)
                    """,
                    (
                        case_id,
                        Jsonb(snapshot.model_dump(mode="json")),
                        Jsonb(model_versions),
                        Jsonb(prompt_versions),
                        system_version,
                    ),
                )
                row = await cursor.fetchone()
        except DatabaseError as error:
            raise InvestigationLockError("investigation lock failed") from error
        if row is None:
            raise InvestigationLockError("investigation lock returned no row")
        return InvestigationLock.model_validate(
            {
                "case_id": row[0],
                "assessment_snapshot": row[1],
                "assessment_hash": row[2],
                "evidence_set_hash": row[3],
                "hash_algorithm": row[4],
                "model_versions": row[5],
                "prompt_versions": row[6],
                "system_version": row[7],
                "locked_at": row[8],
            },
            strict=False,
        )


def _validate_versions(name: str, versions: dict[str, str]) -> None:
    if not versions or any(not key or not value for key, value in versions.items()):
        raise ValueError(f"{name} must contain non-empty names and values")
