from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from casezero_evidence import (
    DocketItem,
    DocumentType,
    ProcessingSource,
    RightsStatus,
    SourceDocument,
    Visibility,
)
from casezero_evidence.artifact_store import LocalArtifactStore
from casezero_ingestion.orchestrator import ProcessingOrchestrator
from casezero_ingestion.registry import ProcessorRegistry
from pydantic import AnyHttpUrl

CASE_ID=UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DOC_ID=UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")
NOW=datetime(2026,8,25,tzinfo=UTC)


class Repository:
    def __init__(self): self.failed=[]
    async def find_successful_run(self,*args): return None
    async def start_processing_run(self,**kwargs): return UUID(int=1)
    async def complete_processing_run(self,*args): pass
    async def fail_processing_run(self,*args): self.failed.append(args)
    async def add_derived_artifact(self,*args): pass
    async def add_structural_units(self,*args): pass


def source() -> ProcessingSource:
    url=AnyHttpUrl("https://data.ntsb.gov/unknown.bin")
    return ProcessingSource(docket_item=DocketItem(case_id=CASE_ID,title="Unknown",source_url=url,document_type=DocumentType.OTHER,rights_status=RightsStatus.NTSB_AUTHORED,attribution="Source: National Transportation Safety Board",review_note="fixture",reviewed_at=NOW),document=SourceDocument(id=DOC_ID,case_id=CASE_ID,title="Unknown",source_url=url,retrieved_at=NOW,document_type=DocumentType.OTHER,visibility=Visibility.INVESTIGATION_EVIDENCE,checksum="a"*64),data=b"\x00\x01")


@pytest.mark.asyncio
async def test_orchestrator_records_unsupported_source_without_aborting(tmp_path: Path) -> None:
    repository=Repository()
    report=await ProcessingOrchestrator(ProcessorRegistry(()),repository,LocalArtifactStore(tmp_path)).process((source(),))
    assert (report.failed,report.succeeded)==(1,0)
    assert repository.failed
