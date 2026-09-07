from uuid import UUID

from casezero_evidence import EvidenceItem
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, JsonValue

from casezero_investigation.events import InvestigationEvent
from casezero_investigation.jobs import FailureCode, InvestigationJob, ModelRequestAttempt
from casezero_investigation.models import Investigation


def _record[T: BaseModel](model: type[T], value: object) -> T:
    if not isinstance(value, dict):
        raise TypeError("database returned an invalid record")
    return model.model_validate(
        {key: value[key] for key in model.model_fields if key in value}, strict=False
    )


class InvestigationRepository:
    def __init__(self, connection: AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def create(self, case_id: UUID, configuration: dict[str, JsonValue]) -> Investigation:
        row = await (await self.connection.execute(
            "select to_jsonb(i) from public.create_investigation(%s,%s) i",
            (case_id, Jsonb(configuration)),
        )).fetchone()
        if row is None:
            raise ValueError("investigation was not created")
        return _record(Investigation, row[0])

    async def get(self, investigation_id: UUID) -> Investigation:
        row = await (await self.connection.execute(
            "select to_jsonb(i) from public.investigations i where id=%s", (investigation_id,),
        )).fetchone()
        if row is None:
            raise LookupError("investigation not accessible")
        return _record(Investigation, row[0])

    async def claim(self, investigation_id: UUID, worker_id: str) -> InvestigationJob | None:
        row = await (await self.connection.execute(
            "select to_jsonb(j) from public.claim_investigation_job(%s,%s) j",
            (investigation_id, worker_id),
        )).fetchone()
        return _record(InvestigationJob, row[0]) if row else None

    async def reserve_request(self, job_id: UUID, worker_id: str, attempt: int) -> ModelRequestAttempt:
        row = await (await self.connection.execute(
            "select to_jsonb(r) from public.reserve_model_request(%s,%s,%s) r",
            (job_id, worker_id, attempt),
        )).fetchone()
        if row is None:
            raise ValueError("model request was not reserved")
        return _record(ModelRequestAttempt, row[0])

    async def events(self, investigation_id: UUID) -> tuple[InvestigationEvent, ...]:
        rows = await (await self.connection.execute(
            "select to_jsonb(e) from public.investigation_events e "
            "where investigation_id=%s order by sequence", (investigation_id,),
        )).fetchall()
        return tuple(_record(InvestigationEvent, row[0]) for row in rows)

    async def evidence(self, investigation_id: UUID) -> tuple[EvidenceItem, ...]:
        rows = await (await self.connection.execute(
            "select e.item from public.investigation_evidence p "
            "join public.evidence_items e on e.id=p.evidence_id and e.model_run_id=p.model_run_id "
            "where p.investigation_id=%s order by e.id", (investigation_id,),
        )).fetchall()
        return tuple(EvidenceItem.model_validate(row[0], strict=False) for row in rows)

    async def fail(self, job: InvestigationJob, worker_id: str, failure: FailureCode) -> None:
        await self.connection.execute(
            "select public.fail_stage_job(%s,%s,%s,%s)",
            (job.id, worker_id, job.attempt_count, failure.value),
        )
