from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from casezero_evidence.candidates import (
    CandidateModel,
    ClaimCandidate,
    EntityCandidate,
    TimelineCandidate,
)
from casezero_evidence.models import EvidenceItem, StrictModel
from pydantic import ConfigDict, Field, field_validator, model_validator, validate_call

from casezero_investigation.models import (
    _FOUR_PLACES,
    Claim,
    ClaimStatus,
    InvestigationEntity,
    TimelineEvent,
    TimePrecision,
    _require_utc,
)

_PROMOTION_RULES = (
    "AI-Generated - Not an Official Finding. All output is provisional and requires expert review. "
    "Do not assign blame, negligence, unlawful conduct, or liability to any person or company. "
    "Cite only the supplied candidate and active evidence IDs; account for every candidate exactly "
    "once and preserve its exact evidence links. Keep unknowns unknown. Do not use external "
    "knowledge or official findings. Do not choose canonical IDs or creation context. "
)
PROMOTION_CLAIMS_PROMPT = (
    "casezero.promotion.claims.v1: " + _PROMOTION_RULES
    + "Preserve each assertion and its OBSERVED, INFERRED, DISPUTED, or UNKNOWN status. "
    "Merge only duplicates with identical casefolded, whitespace-normalized text and status. "
    "Keep independent contradictory claims separate; never swap or drop evidence polarity. "
    "Return bounded model confidence, not a calibrated probability."
)
PROMOTION_ENTITIES_PROMPT = (
    "casezero.promotion.entities.v1: " + _PROMOTION_RULES
    + "Preserve fixed entity types and source spelling. Merge only identities connected by shared "
    "casefolded, whitespace-normalized names or aliases. Select a supplied canonical name or alias "
    "and retain all remaining names as aliases; do not invent names."
)
PROMOTION_TIMELINE_PROMPT = (
    "casezero.promotion.timeline.v1: " + _PROMOTION_RULES
    + "Copy only previously normalized aware UTC timestamps and their original time precision. "
    "Never infer an absolute timestamp from a relative or unknown time. Preserve descriptions; "
    "merge only identical normalized descriptions with identical time and precision. "
    "Return bounded model confidence, not a calibrated probability."
)


def _identity(value: str) -> str:
    return " ".join(value.split()).casefold()


def _require_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


def _require_unique_ids(value: tuple[UUID, ...]) -> tuple[UUID, ...]:
    if len(value) != len(set(value)):
        raise ValueError("reference IDs must be unique")
    return value


class _CandidateDraft(StrictModel):
    source_candidate_ids: tuple[UUID, ...] = Field(min_length=1)

    @field_validator("source_candidate_ids")
    @classmethod
    def require_unique_candidates(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return _require_unique_ids(value)


class ClaimDraft(_CandidateDraft):
    text: str = Field(min_length=1)
    status: ClaimStatus
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    supporting_evidence_ids: tuple[UUID, ...]
    contradicting_evidence_ids: tuple[UUID, ...]

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        return _require_nonblank(value)

    @field_validator("supporting_evidence_ids", "contradicting_evidence_ids")
    @classmethod
    def require_unique_evidence(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return _require_unique_ids(value)

    @model_validator(mode="after")
    def require_evidence(self) -> "ClaimDraft":
        if not self.supporting_evidence_ids and not self.contradicting_evidence_ids:
            raise ValueError("claim draft requires evidence")
        return self


class _EvidenceDraft(_CandidateDraft):
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)

    @field_validator("evidence_ids")
    @classmethod
    def require_unique_evidence(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return _require_unique_ids(value)


class EntityDraft(_EvidenceDraft):
    type: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...]

    @field_validator("type", "canonical_name")
    @classmethod
    def require_text(cls, value: str) -> str:
        return _require_nonblank(value)

    @field_validator("aliases")
    @classmethod
    def require_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for alias in value:
            _require_nonblank(alias)
        return value


class TimelineDraft(_EvidenceDraft):
    occurred_at: datetime | None
    time_precision: TimePrecision
    description: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @field_validator("description")
    @classmethod
    def require_text(cls, value: str) -> str:
        return _require_nonblank(value)

    @field_validator("occurred_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_time_precision(self) -> "TimelineDraft":
        if self.time_precision is TimePrecision.EXACT and self.occurred_at is None:
            raise ValueError("exact time requires occurred_at")
        if (
            self.time_precision in (TimePrecision.UNKNOWN, TimePrecision.RELATIVE)
            and self.occurred_at is not None
        ):
            raise ValueError("unknown or relative time cannot carry an absolute occurred_at")
        return self


class ClaimPromotion(StrictModel):
    claims: tuple[ClaimDraft, ...]


class EntityResolution(StrictModel):
    entities: tuple[EntityDraft, ...]


class TimelinePromotion(StrictModel):
    timeline: tuple[TimelineDraft, ...]


def _promotion_inputs[Candidate: CandidateModel](
    drafts: tuple[_CandidateDraft, ...],
    candidates: tuple[Candidate, ...],
    evidence: tuple[EvidenceItem, ...],
    case_id: UUID,
) -> tuple[dict[UUID, Candidate], set[UUID]]:
    evidence = tuple(EvidenceItem.model_validate(item.model_dump()) for item in evidence)
    candidates_by_id = {item.id: item for item in candidates}
    evidence_ids = {item.id for item in evidence}
    if len(candidates_by_id) != len(candidates):
        raise ValueError("duplicate input candidate IDs")
    if len(evidence_ids) != len(evidence):
        raise ValueError("duplicate active evidence IDs")
    if any(item.case_id != case_id for item in candidates) or any(
        item.case_id != case_id for item in evidence
    ):
        raise ValueError("promotion inputs must belong to the supplied case")
    referenced = tuple(identifier for draft in drafts for identifier in draft.source_candidate_ids)
    if len(referenced) != len(set(referenced)) or set(referenced) != candidates_by_id.keys():
        raise ValueError("every allowed candidate must be accounted for exactly once")
    return candidates_by_id, evidence_ids


def _require_evidence_links(
    proposed: tuple[UUID, ...], expected: set[UUID], active: set[UUID],
) -> None:
    if not expected <= active:
        raise ValueError("candidate references unknown or inactive evidence")
    if set(proposed) != expected:
        raise ValueError("promotion must preserve exact candidate evidence links and polarity")


@validate_call(config=ConfigDict(strict=True))
def materialize_claims(
    output: ClaimPromotion,
    candidates: tuple[ClaimCandidate, ...],
    evidence: tuple[EvidenceItem, ...],
    investigation_id: UUID,
    case_id: UUID,
    model_run_id: UUID,
    created_at: datetime,
) -> tuple[Claim, ...]:
    _require_utc(created_at)
    output = ClaimPromotion.model_validate(output.model_dump())
    candidates = tuple(ClaimCandidate.model_validate(item.model_dump()) for item in candidates)
    candidates_by_id, active = _promotion_inputs(output.claims, candidates, evidence, case_id)
    for draft in output.claims:
        sources = tuple(candidates_by_id[identifier] for identifier in draft.source_candidate_ids)
        if any(candidate.status.value != draft.status.value for candidate in sources):
            raise ValueError("claim promotion cannot change candidate status")
        if any(_identity(candidate.text) != _identity(draft.text) for candidate in sources):
            raise ValueError("claim text must preserve the same assertion; distinct claims cannot merge")
        for field in ("supporting_evidence_ids", "contradicting_evidence_ids"):
            expected: set[UUID] = set()
            for candidate in sources:
                expected.update(_require_unique_ids(getattr(candidate, field)))
            _require_evidence_links(getattr(draft, field), expected, active)
    return tuple(
        Claim(
            investigation_id=investigation_id,
            case_id=case_id,
            text=draft.text,
            status=draft.status,
            confidence=Decimal(str(draft.confidence)).quantize(_FOUR_PLACES, rounding=ROUND_HALF_EVEN),
            source_candidate_ids=tuple(sorted(draft.source_candidate_ids, key=str)),
            supporting_evidence_ids=tuple(sorted(draft.supporting_evidence_ids, key=str)),
            contradicting_evidence_ids=tuple(sorted(draft.contradicting_evidence_ids, key=str)),
            model_run_id=model_run_id,
            created_at=created_at,
        )
        for draft in output.claims
    )


def _entity_names(
    draft: EntityDraft, sources: tuple[EntityCandidate, ...],
) -> tuple[str, tuple[str, ...]]:
    spellings: dict[str, str] = {}
    identities: list[set[str]] = []
    for candidate in sorted(sources, key=lambda item: str(item.id)):
        if candidate.type != draft.type:
            raise ValueError("entity promotion cannot change candidate type")
        names = (candidate.proposed_canonical_name, *candidate.aliases)
        for name in names:
            spellings.setdefault(_identity(_require_nonblank(name)), name)
        identities.append({_identity(name) for name in names})
    connected = identities.pop(0)
    while identities:
        shared = next((names for names in identities if connected & names), None)
        if shared is None:
            raise ValueError("distinct entity identities cannot merge without a shared name or alias")
        connected |= shared
        identities.remove(shared)
    canonical = _identity(draft.canonical_name)
    if canonical not in spellings:
        raise ValueError("canonical name must come from candidate names or aliases")
    if {canonical, *(_identity(alias) for alias in draft.aliases)} != spellings.keys():
        raise ValueError("entity aliases must preserve all candidate names without invention")
    return spellings[canonical], tuple(
        spellings[identity] for identity in sorted(spellings) if identity != canonical
    )


@validate_call(config=ConfigDict(strict=True))
def materialize_entities(
    output: EntityResolution,
    candidates: tuple[EntityCandidate, ...],
    evidence: tuple[EvidenceItem, ...],
    investigation_id: UUID,
    case_id: UUID,
    model_run_id: UUID,
    created_at: datetime,
) -> tuple[InvestigationEntity, ...]:
    _require_utc(created_at)
    output = EntityResolution.model_validate(output.model_dump())
    candidates = tuple(EntityCandidate.model_validate(item.model_dump()) for item in candidates)
    candidates_by_id, active = _promotion_inputs(output.entities, candidates, evidence, case_id)
    names: list[tuple[str, tuple[str, ...]]] = []
    for draft in output.entities:
        sources = tuple(candidates_by_id[identifier] for identifier in draft.source_candidate_ids)
        expected = {
            identifier for candidate in sources
            for identifier in _require_unique_ids(candidate.evidence_ids)
        }
        _require_evidence_links(draft.evidence_ids, expected, active)
        names.append(_entity_names(draft, sources))
    return tuple(
        InvestigationEntity(
            investigation_id=investigation_id,
            case_id=case_id,
            type=draft.type,
            canonical_name=canonical_name,
            aliases=aliases,
            source_candidate_ids=tuple(sorted(draft.source_candidate_ids, key=str)),
            evidence_ids=tuple(sorted(draft.evidence_ids, key=str)),
            model_run_id=model_run_id,
            created_at=created_at,
        )
        for draft, (canonical_name, aliases) in zip(output.entities, names, strict=True)
    )


@validate_call(config=ConfigDict(strict=True))
def materialize_timeline(
    output: TimelinePromotion,
    candidates: tuple[TimelineCandidate, ...],
    evidence: tuple[EvidenceItem, ...],
    investigation_id: UUID,
    case_id: UUID,
    model_run_id: UUID,
    created_at: datetime,
) -> tuple[TimelineEvent, ...]:
    _require_utc(created_at)
    output = TimelinePromotion.model_validate(output.model_dump())
    candidates = tuple(TimelineCandidate.model_validate(item.model_dump()) for item in candidates)
    candidates_by_id, active = _promotion_inputs(output.timeline, candidates, evidence, case_id)
    for draft in output.timeline:
        sources = tuple(candidates_by_id[identifier] for identifier in draft.source_candidate_ids)
        if any(
            candidate.occurred_at != draft.occurred_at
            or candidate.time_precision.value != draft.time_precision.value
            for candidate in sources
        ):
            raise ValueError("timeline promotion must preserve candidate time and precision")
        if any(_identity(candidate.description) != _identity(draft.description) for candidate in sources):
            raise ValueError("distinct timeline descriptions cannot merge or change during promotion")
        expected = {
            identifier for candidate in sources
            for identifier in _require_unique_ids(candidate.evidence_ids)
        }
        _require_evidence_links(draft.evidence_ids, expected, active)
    return tuple(
        TimelineEvent(
            investigation_id=investigation_id,
            case_id=case_id,
            occurred_at=draft.occurred_at,
            time_precision=draft.time_precision,
            description=draft.description,
            confidence=Decimal(str(draft.confidence)).quantize(_FOUR_PLACES, rounding=ROUND_HALF_EVEN),
            source_candidate_ids=tuple(sorted(draft.source_candidate_ids, key=str)),
            evidence_ids=tuple(sorted(draft.evidence_ids, key=str)),
            model_run_id=model_run_id,
            created_at=created_at,
        )
        for draft in output.timeline
    )
