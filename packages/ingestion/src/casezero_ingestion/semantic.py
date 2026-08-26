import json
from typing import Protocol
from uuid import UUID

from casezero_evidence import (
    EvidenceItem,
    EvidenceType,
    ExtractionMethod,
    ProcessingDisposition,
    ReviewStatus,
    StructuralUnit,
)
from casezero_observability import ReasoningRequest, ReasoningResult
from pydantic import BaseModel, ConfigDict, Field


class EvidenceObservation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    structural_unit_id: UUID
    source_document_id: UUID
    type: EvidenceType
    observation: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class EvidenceBatch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    observations: tuple[EvidenceObservation, ...]


class SemanticRouter(Protocol):
    async def generate(self, request: ReasoningRequest[EvidenceBatch], disposition: ProcessingDisposition) -> ReasoningResult[EvidenceBatch]: ...


class SemanticInterpreter:
    def __init__(self, router: SemanticRouter) -> None:
        self._router = router

    async def interpret(self, case_id: UUID, unit: StructuralUnit, disposition: ProcessingDisposition) -> tuple[EvidenceItem, ...]:
        if disposition in {ProcessingDisposition.LINK_ONLY, ProcessingDisposition.EXCLUDED}:
            raise PermissionError(f"semantic processing denied for {disposition.value}")
        prompt = json.dumps({"instruction":"Extract only directly supported observations. Do not invent ids.","allowed_structural_unit_id":str(unit.id),"allowed_source_document_id":str(unit.source_document_id),"locator":unit.locator.model_dump(mode="json"),"payload":unit.payload}, sort_keys=True)
        result = await self._router.generate(
            ReasoningRequest(
                stage="evidence",
                prompt=prompt,
                output_type=EvidenceBatch,
                case_id=case_id,
                structural_unit_ids=(unit.id,),
            ),
            disposition,
        )
        items: list[EvidenceItem] = []
        for observation in result.output.observations:
            if observation.structural_unit_id != unit.id or observation.source_document_id != unit.source_document_id:
                raise ValueError("model invented structural unit or source document id")
            items.append(EvidenceItem(case_id=case_id, source_document_id=unit.source_document_id, structural_unit_id=unit.id, model_run_id=result.run_id, review_status=ReviewStatus.PENDING if observation.confidence < 0.7 else ReviewStatus.NOT_REQUIRED, type=observation.type, observation=observation.observation, source_locator=unit.locator, extraction_method=ExtractionMethod.AI, confidence=observation.confidence))
        return tuple(items)
