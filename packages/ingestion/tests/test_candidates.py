from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from casezero_evidence import (
    EvidenceItem,
    EvidenceType,
    ExtractionMethod,
    PdfLocator,
    ProcessingDisposition,
    TimePrecision,
)
from casezero_ingestion.candidates import CandidateProposer, CandidateSet, ClaimDraft, TimelineDraft
from casezero_observability import ReasoningResult

CASE_ID=UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DOC_ID=UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")


class Router:
    def __init__(self, evidence_id): self.evidence_id=evidence_id
    async def generate(self, request, disposition):
        return ReasoningResult(output=CandidateSet(claims=(ClaimDraft(text="Cable was fractured.",status="OBSERVED",supporting_evidence_ids=(self.evidence_id,),contradicting_evidence_ids=(),confidence=0.8),),entities=(),timeline=()),model_name="fake",fallback_used=False)


def evidence():
    return EvidenceItem(case_id=CASE_ID,source_document_id=DOC_ID,type=EvidenceType.TEXT,observation="Cable was fractured.",source_locator=PdfLocator(page=1),extraction_method=ExtractionMethod.AI,confidence=0.8)


@pytest.mark.asyncio
async def test_candidate_proposer_creates_evidence_bound_claim() -> None:
    item=evidence()
    result=await CandidateProposer(Router(item.id)).propose(CASE_ID,(item,),ProcessingDisposition.AI_ALLOWED,datetime(2026,8,25,tzinfo=UTC))
    assert result[0].supporting_evidence_ids==(item.id,)


@pytest.mark.asyncio
async def test_candidate_proposer_normalizes_aware_timeline_to_utc() -> None:
    item = evidence()

    class TimelineRouter:
        async def generate(self, request, disposition):
            occurred_at = datetime(2022, 8, 18, 12, tzinfo=timezone(timedelta(hours=-7)))
            timeline = TimelineDraft(
                occurred_at=occurred_at,
                time_precision=TimePrecision.EXACT,
                description="Recorded event",
                evidence_ids=(item.id,),
                confidence=0.8,
            )
            return ReasoningResult(
                output=CandidateSet(claims=(), entities=(), timeline=(timeline,)),
                model_name="fake",
                fallback_used=False,
            )

    result = await CandidateProposer(TimelineRouter()).propose(
        CASE_ID,
        (item,),
        ProcessingDisposition.AI_ALLOWED,
        datetime(2026, 8, 25, tzinfo=UTC),
    )

    assert result[0].occurred_at == datetime(2022, 8, 18, 19, tzinfo=UTC)


def test_timeline_draft_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="UTC-aware"):
        TimelineDraft(
            occurred_at=datetime(2022, 8, 18, 12, tzinfo=UTC).replace(tzinfo=None),
            time_precision=TimePrecision.EXACT,
            description="Recorded event",
            evidence_ids=(uuid4(),),
            confidence=0.8,
        )


@pytest.mark.asyncio
async def test_candidate_proposer_rejects_invented_evidence() -> None:
    with pytest.raises(ValueError,match="invented"):
        await CandidateProposer(Router(uuid4())).propose(CASE_ID,(evidence(),),ProcessingDisposition.AI_ALLOWED,datetime(2026,8,25,tzinfo=UTC))
