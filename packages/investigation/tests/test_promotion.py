from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal, localcontext
from uuid import UUID, uuid4

import pytest
from casezero_evidence.candidates import (
    ClaimCandidate,
    EntityCandidate,
    TimelineCandidate,
)
from casezero_evidence.candidates import ClaimStatus as CandidateClaimStatus
from casezero_evidence.candidates import TimePrecision as CandidateTimePrecision
from casezero_evidence.models import EvidenceItem, EvidenceType, ExtractionMethod, TextLocator
from casezero_investigation.models import (
    Claim,
    ClaimStatus,
    InvestigationEntity,
    TimelineEvent,
    TimePrecision,
)
from casezero_investigation.promotion import (
    PROMOTION_CLAIMS_PROMPT,
    PROMOTION_ENTITIES_PROMPT,
    PROMOTION_TIMELINE_PROMPT,
    ClaimDraft,
    ClaimPromotion,
    EntityDraft,
    EntityResolution,
    TimelineDraft,
    TimelinePromotion,
    materialize_claims,
    materialize_entities,
    materialize_timeline,
)
from pydantic import ValidationError

CASE_ID = UUID(int=1)
INVESTIGATION_ID = UUID(int=2)
MODEL_RUN_ID = UUID(int=3)
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
OCCURRED_AT = NOW - timedelta(hours=1)
EVIDENCE_ID = UUID(int=101)
OTHER_EVIDENCE_ID = UUID(int=102)
CANDIDATE_ID = UUID(int=201)


def evidence(identifier: UUID = EVIDENCE_ID, **changes: object) -> EvidenceItem:
    return EvidenceItem.model_validate(
        {
            "id": identifier,
            "case_id": CASE_ID,
            "source_document_id": UUID(int=1001),
            "type": EvidenceType.TEXT,
            "observation": "Synthetic unit-test observation.",
            "source_locator": TextLocator(start=0, end=32),
            "extraction_method": ExtractionMethod.DETERMINISTIC,
        }
        | changes
    )


def claim_candidate(**changes: object) -> ClaimCandidate:
    return ClaimCandidate.model_validate(
        {
            "id": CANDIDATE_ID,
            "case_id": CASE_ID,
            "model_run_id": UUID(int=4),
            "created_at": NOW - timedelta(minutes=1),
            "text": "The recorded speed decreased.",
            "status": CandidateClaimStatus.INFERRED,
            "confidence": 0.33335,
            "supporting_evidence_ids": (EVIDENCE_ID,),
            "contradicting_evidence_ids": (),
        }
        | changes
    )


def claim_draft(**changes: object) -> ClaimDraft:
    return ClaimDraft.model_validate(
        {
            "source_candidate_ids": (CANDIDATE_ID,),
            "text": "The recorded speed decreased.",
            "status": ClaimStatus.INFERRED,
            "confidence": 0.33335,
            "supporting_evidence_ids": (EVIDENCE_ID,),
            "contradicting_evidence_ids": (),
        }
        | changes
    )


def entity_candidate(**changes: object) -> EntityCandidate:
    return EntityCandidate.model_validate(
        {
            "id": CANDIDATE_ID,
            "case_id": CASE_ID,
            "model_run_id": UUID(int=4),
            "created_at": NOW - timedelta(minutes=1),
            "type": "component",
            "proposed_canonical_name": "Fuel Pump",
            "aliases": ("PUMP",),
            "confidence": 0.7,
            "evidence_ids": (EVIDENCE_ID,),
        }
        | changes
    )


def entity_draft(**changes: object) -> EntityDraft:
    return EntityDraft.model_validate(
        {
            "source_candidate_ids": (CANDIDATE_ID,),
            "type": "component",
            "canonical_name": "Fuel Pump",
            "aliases": ("PUMP",),
            "evidence_ids": (EVIDENCE_ID,),
        }
        | changes
    )


def timeline_candidate(**changes: object) -> TimelineCandidate:
    return TimelineCandidate.model_validate(
        {
            "id": CANDIDATE_ID,
            "case_id": CASE_ID,
            "model_run_id": UUID(int=4),
            "created_at": NOW - timedelta(minutes=1),
            "occurred_at": OCCURRED_AT,
            "time_precision": CandidateTimePrecision.EXACT,
            "description": "The recorder sample was captured.",
            "confidence": 0.70005,
            "evidence_ids": (EVIDENCE_ID,),
        }
        | changes
    )


def timeline_draft(**changes: object) -> TimelineDraft:
    return TimelineDraft.model_validate(
        {
            "source_candidate_ids": (CANDIDATE_ID,),
            "occurred_at": OCCURRED_AT,
            "time_precision": TimePrecision.EXACT,
            "description": "The recorder sample was captured.",
            "confidence": 0.70005,
            "evidence_ids": (EVIDENCE_ID,),
        }
        | changes
    )


def test_claim_duplicate_merge_has_exact_polarized_lineage_and_code_context() -> None:
    first = claim_candidate(contradicting_evidence_ids=(OTHER_EVIDENCE_ID,))
    second = claim_candidate(
        id=UUID(int=202),
        text="  THE   RECORDED SPEED DECREASED.  ",
        confidence=0.99,
        supporting_evidence_ids=(UUID(int=103),),
        contradicting_evidence_ids=(OTHER_EVIDENCE_ID,),
    )
    draft = claim_draft(
        source_candidate_ids=(second.id, first.id),
        supporting_evidence_ids=(UUID(int=103), EVIDENCE_ID),
        contradicting_evidence_ids=(OTHER_EVIDENCE_ID,),
    )
    before = (first.model_dump(), second.model_dump(), draft.model_dump())
    rows = materialize_claims(
        ClaimPromotion(claims=(draft,)),
        (second, first),
        (evidence(UUID(int=103)), evidence(OTHER_EVIDENCE_ID), evidence()),
        INVESTIGATION_ID,
        CASE_ID,
        MODEL_RUN_ID,
        NOW,
    )
    assert isinstance(rows, tuple) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, Claim) and row.id.version == 4
    assert row.id not in {first.id, second.id}
    assert row.investigation_id == INVESTIGATION_ID and row.case_id == CASE_ID
    assert row.model_run_id == MODEL_RUN_ID and row.created_at == NOW
    assert row.status is ClaimStatus.INFERRED
    assert row.confidence == Decimal("0.3334")
    assert row.confidence.as_tuple().exponent == -4
    assert row.source_candidate_ids == (first.id, second.id)
    assert row.supporting_evidence_ids == (EVIDENCE_ID, UUID(int=103))
    assert row.contradicting_evidence_ids == (OTHER_EVIDENCE_ID,)
    assert (first.model_dump(), second.model_dump(), draft.model_dump()) == before


def test_independent_opposite_claims_and_dual_polarity_are_not_erased() -> None:
    first = claim_candidate(contradicting_evidence_ids=(EVIDENCE_ID,))
    second = claim_candidate(
        id=UUID(int=202),
        text="The recorded speed did not decrease.",
        status=CandidateClaimStatus.DISPUTED,
        supporting_evidence_ids=(),
        contradicting_evidence_ids=(OTHER_EVIDENCE_ID,),
    )
    output = ClaimPromotion(
        claims=(
            claim_draft(contradicting_evidence_ids=(EVIDENCE_ID,)),
            claim_draft(
                source_candidate_ids=(second.id,),
                text=second.text,
                status=ClaimStatus.DISPUTED,
                supporting_evidence_ids=(),
                contradicting_evidence_ids=(OTHER_EVIDENCE_ID,),
            ),
        )
    )
    rows = materialize_claims(
        output, (first, second), (evidence(), evidence(OTHER_EVIDENCE_ID)),
        INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
    )
    assert len({row.id for row in rows}) == 2
    assert tuple(row.text for row in rows) == (first.text, second.text)
    assert rows[0].supporting_evidence_ids == rows[0].contradicting_evidence_ids
    assert rows[1].supporting_evidence_ids == ()
    assert rows[1].status is ClaimStatus.DISPUTED


@pytest.mark.parametrize("status", list(CandidateClaimStatus))
def test_claim_status_cannot_change_during_promotion(status: CandidateClaimStatus) -> None:
    changed = ClaimStatus.OBSERVED if status is not CandidateClaimStatus.OBSERVED else ClaimStatus.INFERRED
    with pytest.raises(ValueError, match="status"):
        materialize_claims(
            ClaimPromotion(claims=(claim_draft(status=changed),)),
            (claim_candidate(status=status),), (evidence(),),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"supporting_evidence_ids": (), "contradicting_evidence_ids": (EVIDENCE_ID,)},
        {"supporting_evidence_ids": (OTHER_EVIDENCE_ID,)},
        {"supporting_evidence_ids": (EVIDENCE_ID, OTHER_EVIDENCE_ID)},
    ],
)
def test_claim_evidence_cannot_be_repolarized_replaced_or_expanded(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="evidence|polarity"):
        materialize_claims(
            ClaimPromotion(claims=(claim_draft(**changes),)),
            (claim_candidate(),), (evidence(), evidence(OTHER_EVIDENCE_ID)),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


def test_claim_merge_rejects_different_assertions_even_with_same_status() -> None:
    second = claim_candidate(id=UUID(int=202), text="The recorded speed did not decrease.")
    with pytest.raises(ValueError, match="distinct|duplicate|text"):
        materialize_claims(
            ClaimPromotion(claims=(claim_draft(source_candidate_ids=(CANDIDATE_ID, second.id)),)),
            (claim_candidate(), second), (evidence(),),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


def test_claim_text_cannot_reverse_meaning_while_retaining_links() -> None:
    with pytest.raises(ValueError, match="text|assertion"):
        materialize_claims(
            ClaimPromotion(claims=(claim_draft(text="The recorded speed did not decrease."),)),
            (claim_candidate(),), (evidence(),),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize("fault", ["missing", "reused", "invented", "duplicate_input"])
def test_claim_promotion_requires_every_candidate_exactly_once(fault: str) -> None:
    candidates: tuple[ClaimCandidate, ...] = (claim_candidate(),)
    drafts: tuple[ClaimDraft, ...] = (claim_draft(),)
    if fault == "missing":
        drafts = ()
    elif fault == "reused":
        drafts = (claim_draft(), claim_draft())
    elif fault == "invented":
        drafts = (claim_draft(source_candidate_ids=(uuid4(),)),)
    else:
        candidates = (claim_candidate(), claim_candidate())
    with pytest.raises(ValueError, match="candidate"):
        materialize_claims(
            ClaimPromotion(claims=drafts), candidates, (evidence(),),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize("fault", ["inactive", "foreign_candidate", "foreign_evidence", "duplicate_evidence"])
def test_claim_promotion_rejects_invalid_allowed_state(fault: str) -> None:
    candidates: tuple[ClaimCandidate, ...] = (claim_candidate(),)
    active: tuple[EvidenceItem, ...] = (evidence(),)
    if fault == "inactive":
        active = ()
    elif fault == "foreign_candidate":
        candidates = (claim_candidate(case_id=uuid4()),)
    elif fault == "foreign_evidence":
        active = (evidence(case_id=uuid4()),)
    else:
        active = (evidence(), evidence())
    with pytest.raises(ValueError, match="case|evidence"):
        materialize_claims(
            ClaimPromotion(claims=(claim_draft(),)), candidates, active,
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


def test_entity_merge_normalizes_alias_identity_but_preserves_source_spelling() -> None:
    first = entity_candidate()
    second = entity_candidate(
        id=UUID(int=202), proposed_canonical_name=" fuel   pump ",
        aliases=(" pump ",), evidence_ids=(OTHER_EVIDENCE_ID,),
    )
    output = EntityResolution(
        entities=(entity_draft(
            source_candidate_ids=(second.id, first.id), canonical_name="fuel pump",
            aliases=("pump", "  PUMP "), evidence_ids=(OTHER_EVIDENCE_ID, EVIDENCE_ID),
        ),)
    )
    rows = materialize_entities(
        output, (second, first), (evidence(), evidence(OTHER_EVIDENCE_ID)),
        INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
    )
    assert isinstance(rows, tuple) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, InvestigationEntity) and row.id.version == 4
    assert row.canonical_name == "Fuel Pump" and row.aliases == ("PUMP",)
    assert row.identity_aliases == ("pump",)
    assert row.source_candidate_ids == (first.id, second.id)
    assert row.evidence_ids == (EVIDENCE_ID, OTHER_EVIDENCE_ID)
    assert row.investigation_id == INVESTIGATION_ID and row.case_id == CASE_ID
    assert row.model_run_id == MODEL_RUN_ID and row.created_at == NOW


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "person"},
        {"canonical_name": "Unmentioned Component"},
        {"aliases": ("PUMP", "Unmentioned Alias")},
        {"aliases": ()},
        {"evidence_ids": (OTHER_EVIDENCE_ID,)},
        {"evidence_ids": (EVIDENCE_ID, OTHER_EVIDENCE_ID)},
    ],
)
def test_entity_resolution_preserves_fixed_type_names_and_exact_evidence(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="type|name|alias|evidence"):
        materialize_entities(
            EntityResolution(entities=(entity_draft(**changes),)),
            (entity_candidate(),), (evidence(), evidence(OTHER_EVIDENCE_ID)),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


def test_entity_merge_requires_shared_alias_identity() -> None:
    second = entity_candidate(
        id=UUID(int=202), proposed_canonical_name="Pressure Sensor", aliases=(),
    )
    with pytest.raises(ValueError, match="identity|distinct|duplicate"):
        materialize_entities(
            EntityResolution(entities=(entity_draft(
                source_candidate_ids=(CANDIDATE_ID, second.id),
                aliases=("PUMP", "Pressure Sensor"),
            ),)),
            (entity_candidate(), second), (evidence(),),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize("fault", ["missing", "reused", "invented", "duplicate_input", "inactive", "foreign_candidate", "foreign_evidence", "duplicate_evidence"])
def test_entity_resolution_rejects_incomplete_or_invalid_lineage(fault: str) -> None:
    candidates: tuple[EntityCandidate, ...] = (entity_candidate(),)
    drafts: tuple[EntityDraft, ...] = (entity_draft(),)
    active: tuple[EvidenceItem, ...] = (evidence(),)
    if fault == "missing":
        drafts = ()
    elif fault == "reused":
        drafts = (entity_draft(), entity_draft())
    elif fault == "invented":
        drafts = (entity_draft(source_candidate_ids=(uuid4(),)),)
    elif fault == "duplicate_input":
        candidates = (entity_candidate(), entity_candidate())
    elif fault == "inactive":
        active = ()
    elif fault == "foreign_candidate":
        candidates = (entity_candidate(case_id=uuid4()),)
    elif fault == "foreign_evidence":
        active = (evidence(case_id=uuid4()),)
    else:
        active = (evidence(), evidence())
    with pytest.raises(ValueError, match="candidate|case|evidence"):
        materialize_entities(
            EntityResolution(entities=drafts), candidates, active,
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


def test_timeline_duplicate_merge_keeps_preparsed_utc_time_and_quantizes_in_code() -> None:
    first = timeline_candidate()
    second = timeline_candidate(id=UUID(int=202), evidence_ids=(OTHER_EVIDENCE_ID,))
    draft = timeline_draft(
        source_candidate_ids=(second.id, first.id),
        evidence_ids=(OTHER_EVIDENCE_ID, EVIDENCE_ID),
    )
    with localcontext() as context:
        context.rounding = ROUND_UP
        rows = materialize_timeline(
            TimelinePromotion(timeline=(draft,)), (second, first),
            (evidence(), evidence(OTHER_EVIDENCE_ID)),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )
    assert isinstance(rows, tuple) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, TimelineEvent) and row.id.version == 4
    assert row.occurred_at == OCCURRED_AT and row.time_precision is TimePrecision.EXACT
    assert row.confidence == Decimal("0.7000")
    assert row.confidence.as_tuple().exponent == -4
    assert row.source_candidate_ids == (first.id, second.id)
    assert row.evidence_ids == (EVIDENCE_ID, OTHER_EVIDENCE_ID)
    assert row.investigation_id == INVESTIGATION_ID and row.case_id == CASE_ID
    assert row.model_run_id == MODEL_RUN_ID and row.created_at == NOW


@pytest.mark.parametrize("precision", [TimePrecision.UNKNOWN, TimePrecision.RELATIVE, TimePrecision.APPROXIMATE])
def test_ambiguous_timeline_times_remain_unknown(precision: TimePrecision) -> None:
    rows = materialize_timeline(
        TimelinePromotion(timeline=(timeline_draft(occurred_at=None, time_precision=precision),)),
        (timeline_candidate(occurred_at=None, time_precision=CandidateTimePrecision(precision.value)),),
        (evidence(),), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
    )
    assert rows[0].occurred_at is None and rows[0].time_precision is precision


@pytest.mark.parametrize(
    "changes",
    [
        {"occurred_at": OCCURRED_AT + timedelta(seconds=1)},
        {"time_precision": TimePrecision.APPROXIMATE},
        {"evidence_ids": (OTHER_EVIDENCE_ID,)},
        {"evidence_ids": (EVIDENCE_ID, OTHER_EVIDENCE_ID)},
    ],
)
def test_timeline_cannot_invent_time_precision_or_evidence(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="time|precision|evidence"):
        materialize_timeline(
            TimelinePromotion(timeline=(timeline_draft(**changes),)),
            (timeline_candidate(),), (evidence(), evidence(OTHER_EVIDENCE_ID)),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize("precision", [CandidateTimePrecision.UNKNOWN, CandidateTimePrecision.RELATIVE, CandidateTimePrecision.APPROXIMATE])
def test_timeline_cannot_upgrade_ambiguous_candidates_to_exact(precision: CandidateTimePrecision) -> None:
    with pytest.raises(ValueError, match="time|precision"):
        materialize_timeline(
            TimelinePromotion(timeline=(timeline_draft(),)),
            (timeline_candidate(occurred_at=None, time_precision=precision),), (evidence(),),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


def test_timeline_does_not_merge_distinct_simultaneous_events() -> None:
    second = timeline_candidate(id=UUID(int=202), description="A separate sample was captured.")
    with pytest.raises(ValueError, match="distinct|duplicate|description"):
        materialize_timeline(
            TimelinePromotion(timeline=(timeline_draft(source_candidate_ids=(CANDIDATE_ID, second.id)),)),
            (timeline_candidate(), second), (evidence(),),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize("fault", ["missing", "reused", "invented", "duplicate_input", "inactive", "foreign_candidate", "foreign_evidence", "duplicate_evidence"])
def test_timeline_promotion_rejects_incomplete_or_invalid_lineage(fault: str) -> None:
    candidates: tuple[TimelineCandidate, ...] = (timeline_candidate(),)
    drafts: tuple[TimelineDraft, ...] = (timeline_draft(),)
    active: tuple[EvidenceItem, ...] = (evidence(),)
    if fault == "missing":
        drafts = ()
    elif fault == "reused":
        drafts = (timeline_draft(), timeline_draft())
    elif fault == "invented":
        drafts = (timeline_draft(source_candidate_ids=(uuid4(),)),)
    elif fault == "duplicate_input":
        candidates = (timeline_candidate(), timeline_candidate())
    elif fault == "inactive":
        active = ()
    elif fault == "foreign_candidate":
        candidates = (timeline_candidate(case_id=uuid4()),)
    elif fault == "foreign_evidence":
        active = (evidence(case_id=uuid4()),)
    else:
        active = (evidence(), evidence())
    with pytest.raises(ValueError, match="candidate|case|evidence"):
        materialize_timeline(
            TimelinePromotion(timeline=drafts), candidates, active,
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize("field", ["id", "investigation_id", "case_id", "model_run_id", "created_at"])
def test_promotion_drafts_forbid_model_chosen_canonical_context(field: str) -> None:
    for draft in (claim_draft(), entity_draft(), timeline_draft()):
        with pytest.raises(ValidationError, match="Extra inputs"):
            type(draft).model_validate(draft.model_dump() | {field: uuid4()})


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf"), "0.7", True])
def test_promotion_confidence_is_bounded_numeric_model_output(value: object) -> None:
    with pytest.raises(ValidationError):
        claim_draft(confidence=value)
    with pytest.raises(ValidationError):
        timeline_draft(confidence=value)


@pytest.mark.parametrize("field", ["source_candidate_ids", "supporting_evidence_ids", "contradicting_evidence_ids"])
def test_claim_draft_rejects_duplicate_reference_ids(field: str) -> None:
    with pytest.raises(ValidationError, match="unique|duplicate"):
        claim_draft(**{field: (EVIDENCE_ID, EVIDENCE_ID)})


def test_promotion_drafts_require_nonblank_text_and_nonempty_lineage() -> None:
    for field in ("text",):
        with pytest.raises(ValidationError):
            claim_draft(**{field: " \t"})
    for field in ("type", "canonical_name"):
        with pytest.raises(ValidationError):
            entity_draft(**{field: " \t"})
    with pytest.raises(ValidationError):
        entity_draft(aliases=(" \t",))
    with pytest.raises(ValidationError):
        timeline_draft(description=" \t")
    with pytest.raises(ValidationError):
        claim_draft(supporting_evidence_ids=(), contradicting_evidence_ids=())
    for draft in (claim_draft(), entity_draft(), timeline_draft()):
        with pytest.raises(ValidationError):
            type(draft).model_validate(draft.model_dump() | {"source_candidate_ids": ()})
    for draft in (entity_draft(), timeline_draft()):
        with pytest.raises(ValidationError):
            type(draft).model_validate(draft.model_dump() | {"evidence_ids": ()})
        with pytest.raises(ValidationError):
            type(draft).model_validate(draft.model_dump() | {"evidence_ids": (EVIDENCE_ID, EVIDENCE_ID)})


@pytest.mark.parametrize("occurred_at", [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=2)))])
def test_timeline_model_output_only_accepts_aware_utc(occurred_at: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC-aware"):
        timeline_draft(occurred_at=occurred_at)


@pytest.mark.parametrize(
    "precision", [TimePrecision.UNKNOWN, TimePrecision.RELATIVE, TimePrecision.APPROXIMATE]
)
def test_ambiguous_drafts_may_carry_source_anchored_times(precision: TimePrecision) -> None:
    assert timeline_draft(time_precision=precision).occurred_at == OCCURRED_AT
    with pytest.raises(ValidationError, match="time|occurred_at"):
        timeline_draft(occurred_at=None, time_precision=TimePrecision.EXACT)


@pytest.mark.parametrize("created_at", [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=-3)))])
def test_promotion_validates_utc_context_even_for_empty_batches(created_at: datetime) -> None:
    with pytest.raises(ValueError, match="UTC-aware"):
        materialize_claims(ClaimPromotion(claims=()), (), (), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, created_at)
    with pytest.raises(ValueError, match="UTC-aware"):
        materialize_entities(EntityResolution(entities=()), (), (), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, created_at)
    with pytest.raises(ValueError, match="UTC-aware"):
        materialize_timeline(TimelinePromotion(timeline=()), (), (), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, created_at)


def test_empty_promotion_outputs_are_valid_only_with_no_candidates() -> None:
    assert materialize_claims(ClaimPromotion(claims=()), (), (), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW) == ()
    assert materialize_entities(EntityResolution(entities=()), (), (), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW) == ()
    assert materialize_timeline(TimelinePromotion(timeline=()), (), (), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW) == ()


def test_promotion_contracts_round_trip_json_but_reject_python_lists_and_extra_fields() -> None:
    outputs = (
        ClaimPromotion(claims=(claim_draft(),)),
        EntityResolution(entities=(entity_draft(),)),
        TimelinePromotion(timeline=(timeline_draft(),)),
    )
    for output in outputs:
        assert type(output).model_validate_json(output.model_dump_json()) == output
        with pytest.raises(ValidationError, match="Extra inputs"):
            type(output).model_validate(output.model_dump() | {"official_finding": "not allowed"})
        values = output.model_dump()
        field = next(iter(values))
        with pytest.raises(ValidationError):
            type(output).model_validate(values | {field: list(values[field])})


def test_promotion_revalidates_mutated_drafts_and_input_timestamps() -> None:
    draft = claim_draft()
    draft.confidence = 2.0
    with pytest.raises(ValidationError):
        materialize_claims(
            ClaimPromotion(claims=(draft,)), (claim_candidate(),), (evidence(),),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )
    candidate = timeline_candidate()
    candidate.occurred_at = OCCURRED_AT.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="UTC-aware"):
        materialize_timeline(
            TimelinePromotion(timeline=(timeline_draft(),)), (candidate,), (evidence(),),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


def test_entity_prompt_never_merges_different_fixed_types() -> None:
    assert "Never merge candidates with different type strings" in PROMOTION_ENTITIES_PROMPT


@pytest.mark.parametrize(
    ("prompt", "prefix"),
    [
        (PROMOTION_CLAIMS_PROMPT, "casezero.promotion.claims.v1:"),
        (PROMOTION_ENTITIES_PROMPT, "casezero.promotion.entities.v1:"),
        (PROMOTION_TIMELINE_PROMPT, "casezero.promotion.timeline.v1:"),
    ],
)
def test_promotion_prompts_are_versioned_provisional_and_id_grounded(prompt: str, prefix: str) -> None:
    assert prompt.startswith(prefix)
    for instruction in ("provisional", "not an official finding", "cite", "ids", "unknown", "blame"):
        assert instruction in prompt.casefold()
    assert "CEN22FA375" not in prompt
