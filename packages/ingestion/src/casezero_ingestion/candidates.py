import json
import re
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from casezero_evidence import (
    ClaimCandidate,
    ClaimStatus,
    EntityCandidate,
    EvidenceItem,
    ProcessingDisposition,
    TimelineCandidate,
    TimePrecision,
)
from casezero_observability import ReasoningRequest, ReasoningResult
from pydantic import BaseModel, Field, field_validator

CANDIDATE_PROMPT_TEMPLATE = (
    "casezero.candidates.v2: Propose only investigation-material candidates supported by supplied "
    "evidence IDs. A candidate must describe a meaningful event, condition, entity, assertion, or "
    "contradiction that could distinguish causal explanations. Do not turn unlabeled table values, "
    "isolated scalar measurements, row/column coordinates, locator text, or generic observations into "
    "claims, entities, or timeline events. Preserve OBSERVED, INFERRED, DISPUTED, and UNKNOWN."
)
_SCALAR = re.compile(r"[-+]?\d+(?:\.\d+)?(?:\s*[A-Za-z%]+)?")
_LOCATOR_RESTATEMENT = re.compile(r"\brow\s+\d+\b.*\bcolumn\s+\d+\b", re.IGNORECASE)
_GENERIC_OBSERVATION = re.compile(
    r"^(?:numerical\s+)?observation\b.*(?:recorded|documented|row|column|cell)", re.IGNORECASE,
)


def _is_material_candidate_text(value: str) -> bool:
    text = " ".join(value.split())
    return bool(text) and _SCALAR.fullmatch(text) is None and not (
        _LOCATOR_RESTATEMENT.search(text) or _GENERIC_OBSERVATION.search(text)
    )


class ClaimDraft(BaseModel):
    text: str
    status: ClaimStatus
    supporting_evidence_ids: tuple[UUID, ...]
    contradicting_evidence_ids: tuple[UUID, ...]
    confidence: float=Field(ge=0,le=1)


class EntityDraft(BaseModel):
    type: str
    proposed_canonical_name: str
    aliases: tuple[str,...]=()
    evidence_ids: tuple[UUID,...]
    confidence: float=Field(ge=0,le=1)


class TimelineDraft(BaseModel):
    occurred_at: datetime|None
    time_precision: TimePrecision
    description: str
    evidence_ids: tuple[UUID,...]
    confidence: float=Field(ge=0,le=1)


    @field_validator("occurred_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must be UTC-aware")
        return value.astimezone(UTC) if value is not None else None


class CandidateSet(BaseModel):
    claims: tuple[ClaimDraft,...]
    entities: tuple[EntityDraft,...]
    timeline: tuple[TimelineDraft,...]


class CandidateRouter(Protocol):
    async def generate(self, request: ReasoningRequest[CandidateSet], disposition: ProcessingDisposition) -> ReasoningResult[CandidateSet]: ...


class CandidateProposer:
    def __init__(self, router: CandidateRouter) -> None:
        self._router = router

    async def propose(self, case_id: UUID, evidence: tuple[EvidenceItem, ...], disposition: ProcessingDisposition, created_at: datetime) -> tuple[ClaimCandidate | EntityCandidate | TimelineCandidate, ...]:
        allowed = {item.id for item in evidence}
        payload = [{"id": str(item.id), "observation": item.observation, "type": item.type.value, "confidence": item.confidence} for item in evidence]
        request = ReasoningRequest(
            stage="candidates",
            prompt=json.dumps(
                {"instruction": CANDIDATE_PROMPT_TEMPLATE, "evidence": payload},
                sort_keys=True,
            ),
            prompt_template=CANDIDATE_PROMPT_TEMPLATE,
            output_type=CandidateSet,
            case_id=case_id,
            structural_unit_ids=tuple(
                dict.fromkeys(
                    item.structural_unit_id
                    for item in evidence
                    if item.structural_unit_id is not None
                )
            ),
        )
        result = await self._router.generate(request, disposition)
        output: list[ClaimCandidate | EntityCandidate | TimelineCandidate] = []
        for claim_draft in result.output.claims:
            ids = set(claim_draft.supporting_evidence_ids + claim_draft.contradicting_evidence_ids)
            if not ids <= allowed:
                raise ValueError("model invented evidence id")
            if _is_material_candidate_text(claim_draft.text):
                output.append(ClaimCandidate(case_id=case_id, text=claim_draft.text, status=claim_draft.status, supporting_evidence_ids=claim_draft.supporting_evidence_ids, contradicting_evidence_ids=claim_draft.contradicting_evidence_ids, confidence=claim_draft.confidence, model_run_id=result.run_id, created_at=created_at))
        for entity_draft in result.output.entities:
            if not set(entity_draft.evidence_ids) <= allowed:
                raise ValueError("model invented evidence id")
            if _is_material_candidate_text(entity_draft.proposed_canonical_name):
                output.append(EntityCandidate(case_id=case_id, type=entity_draft.type, proposed_canonical_name=entity_draft.proposed_canonical_name, aliases=entity_draft.aliases, evidence_ids=entity_draft.evidence_ids, confidence=entity_draft.confidence, model_run_id=result.run_id, created_at=created_at))
        for timeline_draft in result.output.timeline:
            if not set(timeline_draft.evidence_ids) <= allowed:
                raise ValueError("model invented evidence id")
            if _is_material_candidate_text(timeline_draft.description):
                output.append(TimelineCandidate(case_id=case_id, occurred_at=timeline_draft.occurred_at.astimezone(UTC) if timeline_draft.occurred_at is not None else None, time_precision=timeline_draft.time_precision, description=timeline_draft.description, evidence_ids=timeline_draft.evidence_ids, confidence=timeline_draft.confidence, model_run_id=result.run_id, created_at=created_at))
        return tuple(output)
