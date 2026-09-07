import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from casezero_evidence import (
    ClaimCandidate,
    ClaimStatus,
    DerivedArtifact,
    DerivedArtifactKind,
    DocketItem,
    DocumentType,
    EntityCandidate,
    EvidenceItem,
    EvidenceType,
    ExtractionMethod,
    RightsStatus,
    SourceDocument,
    StructuralUnit,
    StructuralUnitKind,
    TextLocator,
    TimelineCandidate,
    TimePrecision,
    Visibility,
)
from casezero_evidence.repository import EvidenceRepository
from psycopg import AsyncConnection
from pydantic import AnyHttpUrl

NOW = datetime(2026, 9, 7, tzinfo=UTC)


async def seed_case(
    connection: AsyncConnection, *, complete: bool = False,
) -> tuple[UUID, EvidenceItem, ClaimCandidate]:
    case_id = uuid4()
    url = AnyHttpUrl(f"https://example.test/{case_id}.txt")
    checksum = hashlib.sha256(case_id.bytes).hexdigest()
    await connection.execute(
        "insert into cases(id, ntsb_number, title, state, evidence_cutoff) "
        "values (%s, %s, 'synthetic investigation fixture', 'BLIND', %s)",
        (case_id, f"TEST-{case_id}", NOW),
    )
    repository = EvidenceRepository(connection)
    docket = DocketItem(
        case_id=case_id, title="Factual observation", source_url=url,
        document_type=DocumentType.FACTUAL_REPORT, rights_status=RightsStatus.NTSB_AUTHORED,
        attribution="Synthetic test", review_note="Synthetic test", reviewed_at=NOW,
        published_at=NOW, expected_checksum=checksum,
    )
    docket_id = await repository.upsert_docket_item(docket)
    source = SourceDocument(
        case_id=case_id, title=docket.title, source_url=url, published_at=NOW,
        retrieved_at=NOW, document_type=DocumentType.FACTUAL_REPORT,
        visibility=Visibility.INVESTIGATION_EVIDENCE, checksum=checksum,
    )
    await repository.link_source_document(source, docket_id, f"test/{checksum}", 8)
    processing_id = await repository.start_processing_run(
        source_document_id=source.id, source_checksum=checksum,
        processor_name="test", processor_version="1", configuration_hash=checksum,
        started_at=NOW,
    )
    artifact = DerivedArtifact(
        processing_run_id=processing_id, source_document_id=source.id,
        kind=DerivedArtifactKind.DOCUMENT_STRUCTURE, checksum=checksum,
        storage_path=f"test/derived/{checksum}", media_type="text/plain", byte_size=8,
        created_at=NOW,
    )
    unit = StructuralUnit(
        derived_artifact_id=artifact.id, source_document_id=source.id,
        kind=StructuralUnitKind.TEXT_BLOCK, ordinal=0, content_checksum=checksum,
        locator=TextLocator(start=0, end=8), payload={"text": "fixture evidence"},
    )
    await repository.add_artifact_with_units(artifact, (unit,))
    await repository.complete_processing_run(processing_id, NOW)
    run_id = uuid4()
    await repository.record_model_run(
        run_id=run_id, case_id=case_id, parent_run_id=None, stage="evidence",
        provider="test", model="fixture", prompt_hash=checksum,
        structural_unit_ids=(unit.id,), input_tokens=1, output_tokens=1,
        latency_ms=1, retry_count=0, schema_failure_count=0, status="SUCCEEDED", created_at=NOW,
    )
    item = EvidenceItem(
        case_id=case_id, source_document_id=source.id, structural_unit_id=unit.id,
        model_run_id=run_id, type=EvidenceType.TEXT, observation="Power decreased before landing.",
        source_locator=unit.locator, extraction_method=ExtractionMethod.AI, confidence=0.9,
    )
    items = (item,)
    if complete:
        items += (item.model_copy(update={"id": uuid4(), "observation": "Power recovered."}),)
    await repository.persist_semantic_result(unit.id, items, checksum, NOW)
    candidate_run = uuid4()
    await repository.record_model_run(
        run_id=candidate_run, case_id=case_id, parent_run_id=None, stage="candidates",
        provider="test", model="fixture", prompt_hash=checksum,
        structural_unit_ids=(unit.id,), input_tokens=1, output_tokens=1,
        latency_ms=1, retry_count=0, schema_failure_count=0, status="SUCCEEDED", created_at=NOW,
    )
    candidate = ClaimCandidate(
        case_id=case_id, text=item.observation, status=ClaimStatus.OBSERVED,
        supporting_evidence_ids=(item.id,), confidence=0.9, model_run_id=candidate_run,
        created_at=NOW,
    )
    candidates: tuple[ClaimCandidate | EntityCandidate | TimelineCandidate, ...] = (candidate,)
    if complete:
        candidates += (
            EntityCandidate(
                case_id=case_id, type="component", proposed_canonical_name="engine",
                aliases=("powerplant",), evidence_ids=(item.id,), confidence=0.9,
                model_run_id=candidate_run, created_at=NOW,
            ),
            *(TimelineCandidate(
                case_id=case_id, occurred_at=NOW, time_precision=TimePrecision.EXACT,
                description=description, evidence_ids=(item.id,), confidence=0.9,
                model_run_id=candidate_run, created_at=NOW,
            ) for description in ("Power decreased.", "Power recovered.")),
        )
    await repository.persist_candidate_batch(
        case_id, (unit.id,), checksum, checksum, candidates, NOW,
    )
    return case_id, item, candidate
