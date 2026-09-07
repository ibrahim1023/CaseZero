from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from casezero_evidence import EvidenceItem, SourceDocument
from casezero_evidence.access import PostgresAccessAuditRecorder
from casezero_evidence.blindness import (
    AccessAuditEvent,
    AccessCapability,
    AccessOperation,
    AuditReasonCode,
    RuntimeActor,
    WorkflowStage,
)
from casezero_evidence.models import StrictModel
from casezero_retrieval.models import EvidenceSearchQuery, EvidenceSearchResult, RetrievalIntent
from casezero_retrieval.repository import RetrievalRepository
from pydantic import Field

from casezero_investigation.jobs import InvestigationJob
from casezero_investigation.models import Investigation, InvestigationStage
from casezero_investigation.replay import InvestigationProjection
from casezero_investigation.repository import CandidateBatch, InvestigationRepository


class SearchDraft(StrictModel):
    query_text: str = Field(min_length=1, max_length=4096)


class InvestigationTools:
    def __init__(
        self, repository: InvestigationRepository, investigation: Investigation,
        job: InvestigationJob, worker_id: str, parent_span_id: UUID,
    ) -> None:
        if job.investigation_id != investigation.id:
            raise ValueError("tool context mismatch")
        self._repository = repository
        self._investigation = investigation
        self._job = job
        self._worker_id = worker_id
        self._parent_span_id = parent_span_id
        self._audit = PostgresAccessAuditRecorder(repository.connection)

    async def _record(self, capability: AccessCapability, document_id: UUID | None = None) -> None:
        await self._audit.record(AccessAuditEvent(
            case_id=self._investigation.case_id, stage=WorkflowStage.BLIND,
            actor_role=RuntimeActor.BLIND, capability=capability, operation=AccessOperation.READ,
            target_document_id=document_id, allowed=True,
            reason_code=AuditReasonCode.ALLOWED_EVIDENCE_READ, occurred_at=datetime.now(UTC),
        ))

    @asynccontextmanager
    async def _span(self, operation: str, *, retrieval: bool = False) -> AsyncIterator[None]:
        if await (await self._repository.connection.execute("select current_user")).fetchone() != ("casezero_blind",):
            raise PermissionError("tools require a casezero_blind connection")
        await self._repository.assert_lease(self._job, self._worker_id)
        span = await self._repository.start_span(
            self._job, self._worker_id, "RETRIEVAL" if retrieval else "TOOL", operation,
            "fts-v1" if retrieval else "investigation-tools-v1", self._parent_span_id,
        )
        try:
            await self._record(AccessCapability.INVESTIGATION_TOOL)
            yield
        except Exception:
            await self._repository.finish_span(span, self._job, self._worker_id, "FAILED")
            raise
        else:
            await self._repository.finish_span(span, self._job, self._worker_id, "SUCCEEDED")

    async def candidate_batch(self) -> CandidateBatch:
        async with self._span("candidate_batch"):
            batch = await self._repository.candidate_batch(self._job)
            evidence_ids = {item.id for item in await self.get_evidence()}
            references = {eid for c in batch.claims for eid in (*c.supporting_evidence_ids, *c.contradicting_evidence_ids)}
            references.update(eid for e in batch.entities for eid in e.evidence_ids)
            references.update(eid for t in batch.timeline for eid in t.evidence_ids)
            if not references <= evidence_ids:
                raise PermissionError("candidate evidence unavailable")
            return batch

    async def get_state(self) -> InvestigationProjection:
        async with self._span("get_state"):
            state = await self._repository.projection(self._investigation.id)
            if state.case_id != self._investigation.case_id:
                raise PermissionError("state context mismatch")
            evidence_ids = {item.id for item in await self.get_evidence()}
            references = {eid for c in state.claims.values() for eid in (*c.supporting_evidence_ids, *c.contradicting_evidence_ids)}
            references.update(eid for e in state.entities.values() for eid in e.evidence_ids)
            references.update(eid for t in state.timeline.values() for eid in t.evidence_ids)
            references.update(r.evidence_id for query in state.retrievals.values() for r in query.results)
            references.update(eid for test in state.tests.values() for eid in (*test.evidence_ids, *test.result_evidence_ids))
            if not references <= evidence_ids:
                raise PermissionError("canonical evidence unavailable")
            return state

    async def get_evidence(self) -> tuple[EvidenceItem, ...]:
        async with self._span("get_evidence"):
            evidence = await self._repository.evidence(self._investigation.id)
            for document_id in sorted({item.source_document_id for item in evidence}):
                await self._repository.source_metadata(self._investigation.id, document_id)
                await self._record(AccessCapability.EVIDENCE_READ, document_id)
            if any(item.case_id != self._investigation.case_id for item in evidence):
                raise PermissionError("evidence context mismatch")
            return evidence

    async def get_source_document(self, document_id: UUID) -> SourceDocument:
        async with self._span("get_source_document"):
            evidence = await self._repository.evidence(self._investigation.id)
            if document_id not in {item.source_document_id for item in evidence}:
                raise PermissionError("source evidence unavailable")
            source = await self._repository.source_metadata(self._investigation.id, document_id)
            await self._record(AccessCapability.EVIDENCE_READ, source.id)
            return source

    def search_query(self, draft: SearchDraft) -> EvidenceSearchQuery:
        if self._job.stage not in {InvestigationStage.SEARCH_SUPPORT, InvestigationStage.SEARCH_CONTRADICTIONS}:
            raise ValueError("search outside retrieval stage")
        return EvidenceSearchQuery(
            investigation_id=self._investigation.id, case_id=self._investigation.case_id,
            intent=RetrievalIntent.SUPPORT if self._job.stage is InvestigationStage.SEARCH_SUPPORT else RetrievalIntent.CONTRADICT,
            query_text=draft.query_text, limit=50,
        )

    async def search(self, draft: SearchDraft) -> tuple[EvidenceSearchQuery, tuple[EvidenceSearchResult, ...]]:
        async with self._span("search_evidence", retrieval=True):
            query = self.search_query(draft)
            results = await RetrievalRepository(self._repository.connection).search(query)
            evidence = {item.id: item for item in await self._repository.evidence(self._investigation.id)}
            for document_id in sorted({evidence[result.evidence_id].source_document_id for result in results}):
                await self._repository.source_metadata(self._investigation.id, document_id)
                await self._record(AccessCapability.RETRIEVAL, document_id)
            return query, results
