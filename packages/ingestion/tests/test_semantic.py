from uuid import UUID, uuid4

import pytest
from casezero_evidence import (
    EvidenceType,
    PdfLocator,
    ProcessingDisposition,
    ReviewStatus,
    StructuralUnit,
    StructuralUnitKind,
)
from casezero_ingestion.semantic import EvidenceBatch, EvidenceObservation, SemanticInterpreter
from casezero_observability import ReasoningResult

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DOC_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")
ARTIFACT_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e72")


class FakeRouter:
    def __init__(self, structural_unit_id: UUID) -> None:
        self.structural_unit_id = structural_unit_id

    async def generate(self, request, disposition):
        output = EvidenceBatch(observations=(EvidenceObservation(structural_unit_id=self.structural_unit_id, source_document_id=DOC_ID, type=EvidenceType.TEXT, observation="Cable was fractured after impact.", confidence=0.6),))
        return ReasoningResult(output=output, model_name="fake", fallback_used=False)


def unit() -> StructuralUnit:
    return StructuralUnit(derived_artifact_id=ARTIFACT_ID, source_document_id=DOC_ID, kind=StructuralUnitKind.TEXT_BLOCK, ordinal=0, content_checksum="a" * 64, locator=PdfLocator(page=1), payload={"text":"Cable was fractured after impact."})


@pytest.mark.asyncio
async def test_interpreter_creates_grounded_pending_review_evidence() -> None:
    structural_unit = unit()
    items = await SemanticInterpreter(FakeRouter(structural_unit.id)).interpret(CASE_ID, structural_unit, ProcessingDisposition.AI_ALLOWED)
    assert len(items) == 1
    assert items[0].structural_unit_id == structural_unit.id
    assert items[0].source_locator == structural_unit.locator
    assert items[0].review_status is ReviewStatus.PENDING
    assert items[0].entities == ()


@pytest.mark.asyncio
async def test_interpreter_rejects_invented_unit_id() -> None:
    with pytest.raises(ValueError, match="invented"):
        await SemanticInterpreter(FakeRouter(uuid4())).interpret(CASE_ID, unit(), ProcessingDisposition.AI_ALLOWED)
