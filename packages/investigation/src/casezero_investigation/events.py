from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from casezero_evidence.models import StrictModel
from casezero_retrieval.models import EvidenceSearchQuery, EvidenceSearchResult
from pydantic import Field, JsonValue, field_validator, model_validator

from casezero_investigation.canonical import canonical_digest, postgres_jsonb_text
from casezero_investigation.hypotheses import (
    ConfidenceRevision,
    HypothesisCritique,
    HypothesisTest,
    HypothesisTestDraft,
    HypothesisTestStatus,
)
from casezero_investigation.models import (
    Claim,
    Hypothesis,
    HypothesisStatus,
    InvestigationEntity,
    InvestigationStage,
    InvestigationStatus,
    TimelineEvent,
    UnresolvedQuestion,
    _require_utc,
)

EventType = Literal[
    "INVESTIGATION_STARTED",
    "STAGE_TRANSITION",
    "ENTITY_CREATED",
    "TIMELINE_CREATED",
    "CLAIM_CREATED",
    "HYPOTHESIS_CREATED",
    "CRITIQUE_CREATED",
    "TEST_CREATED",
    "TEST_RESOLVED",
    "CONFIDENCE_REVISED",
    "HYPOTHESIS_STATUS_CHANGED",
    "RETRIEVAL_COMPLETED",
    "INVESTIGATION_COMPLETED",
]
EventTargetType = Literal[
    "investigation", "entity", "timeline_event", "claim", "hypothesis", "critique",
    "test", "confidence_revision", "retrieval_query",
]
_TARGET_TYPES: dict[EventType, EventTargetType] = {
    "INVESTIGATION_STARTED": "investigation",
    "STAGE_TRANSITION": "investigation",
    "ENTITY_CREATED": "entity",
    "TIMELINE_CREATED": "timeline_event",
    "CLAIM_CREATED": "claim",
    "HYPOTHESIS_CREATED": "hypothesis",
    "CRITIQUE_CREATED": "critique",
    "TEST_CREATED": "test",
    "TEST_RESOLVED": "test",
    "CONFIDENCE_REVISED": "confidence_revision",
    "HYPOTHESIS_STATUS_CHANGED": "hypothesis",
    "RETRIEVAL_COMPLETED": "retrieval_query",
    "INVESTIGATION_COMPLETED": "investigation",
}


def _event_data(value: object) -> object:
    if isinstance(value, StrictModel):
        value = vars(value)
    if isinstance(value, dict):
        return {key: _event_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_event_data(item) for item in value)
    if isinstance(value, list):
        return [_event_data(item) for item in value]
    return value


def _ordered_ids(value: tuple[UUID, ...]) -> tuple[UUID, ...]:
    if len(value) != len(set(value)):
        raise ValueError("reference IDs must be unique")
    return tuple(sorted(value))


def _ordered_text(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(" ".join(item.split()).casefold() for item in value)
    if any(not item for item in normalized):
        raise ValueError("set-like text must not be blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError("set-like text must be unique")
    return tuple(item for _, item in sorted(zip(normalized, value, strict=True)))


def _ordered_test[T: HypothesisTestDraft](value: T) -> T:
    value = type(value).model_validate(_event_data(value))
    updates = {
        "evidence_ids": _ordered_ids(value.evidence_ids),
        "claim_ids": _ordered_ids(value.claim_ids),
    }
    if isinstance(value, HypothesisTest):
        updates["result_evidence_ids"] = _ordered_ids(value.result_evidence_ids)
    return value.model_copy(update=updates)


class _VersionedPayload(StrictModel):
    schema_version: Literal["1"] = "1"


class InvestigationStartedPayload(_VersionedPayload):
    kind: Literal["INVESTIGATION_STARTED"] = "INVESTIGATION_STARTED"
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_stage: InvestigationStage
    status: InvestigationStatus

    @model_validator(mode="after")
    def require_initial_state(self) -> "InvestigationStartedPayload":
        if (
            self.current_stage is not InvestigationStage.PROMOTE_TIMELINE
            or self.status is not InvestigationStatus.PENDING
        ):
            raise ValueError("investigation must start PENDING at PROMOTE_TIMELINE")
        return self


class StageTransitionPayload(_VersionedPayload):
    kind: Literal["STAGE_TRANSITION"] = "STAGE_TRANSITION"
    previous_stage: InvestigationStage
    next_stage: InvestigationStage

    @model_validator(mode="after")
    def require_stage_order(self) -> "StageTransitionPayload":
        stages = tuple(InvestigationStage)
        initial_start = self.previous_stage is self.next_stage is stages[0]
        if not initial_start and stages.index(self.next_stage) != stages.index(self.previous_stage) + 1:
            raise ValueError("stage transition must follow declared stage order")
        return self


class EntityCreatedPayload(_VersionedPayload):
    kind: Literal["ENTITY_CREATED"] = "ENTITY_CREATED"
    record: InvestigationEntity

    @field_validator("record")
    @classmethod
    def order_record(cls, value: InvestigationEntity) -> InvestigationEntity:
        value = InvestigationEntity.model_validate(_event_data(value))
        return value.model_copy(update={
            "aliases": _ordered_text(value.aliases),
            "source_candidate_ids": _ordered_ids(value.source_candidate_ids),
            "evidence_ids": _ordered_ids(value.evidence_ids),
        })


class TimelineCreatedPayload(_VersionedPayload):
    kind: Literal["TIMELINE_CREATED"] = "TIMELINE_CREATED"
    record: TimelineEvent

    @field_validator("record")
    @classmethod
    def order_record(cls, value: TimelineEvent) -> TimelineEvent:
        value = TimelineEvent.model_validate(_event_data(value))
        return value.model_copy(update={
            "source_candidate_ids": _ordered_ids(value.source_candidate_ids),
            "evidence_ids": _ordered_ids(value.evidence_ids),
        })


class ClaimCreatedPayload(_VersionedPayload):
    kind: Literal["CLAIM_CREATED"] = "CLAIM_CREATED"
    record: Claim

    @field_validator("record")
    @classmethod
    def order_record(cls, value: Claim) -> Claim:
        value = Claim.model_validate(_event_data(value))
        return value.model_copy(update={
            "source_candidate_ids": _ordered_ids(value.source_candidate_ids),
            "supporting_evidence_ids": _ordered_ids(value.supporting_evidence_ids),
            "contradicting_evidence_ids": _ordered_ids(value.contradicting_evidence_ids),
        })


class HypothesisCreatedPayload(_VersionedPayload):
    kind: Literal["HYPOTHESIS_CREATED"] = "HYPOTHESIS_CREATED"
    record: Hypothesis
    supporting_claim_ids: tuple[UUID, ...] = ()
    contradicting_claim_ids: tuple[UUID, ...] = ()
    questions: tuple[UnresolvedQuestion, ...] = ()

    @field_validator("record")
    @classmethod
    def validate_record(cls, value: Hypothesis) -> Hypothesis:
        value = Hypothesis.model_validate(_event_data(value))
        if value.status is not HypothesisStatus.ACTIVE or value.current_confidence != value.initial_confidence:
            raise ValueError("hypothesis must start ACTIVE at its initial confidence")
        return value

    @field_validator("supporting_claim_ids", "contradicting_claim_ids")
    @classmethod
    def order_claim_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return _ordered_ids(value)

    @field_validator("questions")
    @classmethod
    def order_questions(cls, value: tuple[UnresolvedQuestion, ...]) -> tuple[UnresolvedQuestion, ...]:
        value = tuple(UnresolvedQuestion.model_validate(_event_data(question)) for question in value)
        _ordered_ids(tuple(question.id for question in value))
        _ordered_text(tuple(question.text for question in value))
        return tuple(sorted(value, key=lambda question: question.id))

    @model_validator(mode="after")
    def require_links_and_question_context(self) -> "HypothesisCreatedPayload":
        if not self.supporting_claim_ids and not self.contradicting_claim_ids:
            raise ValueError("hypothesis creation requires at least one claim link")
        if any(
            question.hypothesis_id != self.record.id
            or question.investigation_id != self.record.investigation_id
            or question.case_id != self.record.case_id
            for question in self.questions
        ):
            raise ValueError("question context must match the created hypothesis")
        return self


class CritiqueCreatedPayload(_VersionedPayload):
    kind: Literal["CRITIQUE_CREATED"] = "CRITIQUE_CREATED"
    record: HypothesisCritique

    @field_validator("record")
    @classmethod
    def order_record(cls, value: HypothesisCritique) -> HypothesisCritique:
        value = HypothesisCritique.model_validate(_event_data(value))
        tests = tuple(_ordered_test(test) for test in value.proposed_tests)
        keys = tuple(postgres_jsonb_text(test.model_dump(mode="json")) for test in tests)
        if len(keys) != len(set(keys)):
            raise ValueError("proposed tests must be unique")
        return value.model_copy(update={
            "missing_evidence": _ordered_text(value.missing_evidence),
            "proposed_tests": tuple(test for _, test in sorted(zip(keys, tests, strict=True))),
        })


class TestCreatedPayload(_VersionedPayload):
    kind: Literal["TEST_CREATED"] = "TEST_CREATED"
    record: HypothesisTest

    @field_validator("record")
    @classmethod
    def require_pending_test(cls, value: HypothesisTest) -> HypothesisTest:
        value = _ordered_test(value)
        if value.status is not HypothesisTestStatus.PENDING:
            raise ValueError("created test must be PENDING")
        return value


class TestResolvedPayload(_VersionedPayload):
    kind: Literal["TEST_RESOLVED"] = "TEST_RESOLVED"
    record: HypothesisTest

    @field_validator("record")
    @classmethod
    def require_resolved_test(cls, value: HypothesisTest) -> HypothesisTest:
        value = _ordered_test(value)
        if value.status not in (HypothesisTestStatus.SUCCEEDED, HypothesisTestStatus.FAILED):
            raise ValueError("resolved test must be SUCCEEDED or FAILED")
        return value


class ConfidenceRevisedPayload(_VersionedPayload):
    kind: Literal["CONFIDENCE_REVISED"] = "CONFIDENCE_REVISED"
    record: ConfidenceRevision

    @field_validator("record")
    @classmethod
    def validate_record(cls, value: ConfidenceRevision) -> ConfidenceRevision:
        return ConfidenceRevision.model_validate(_event_data(value))


class HypothesisStatusChangedPayload(_VersionedPayload):
    kind: Literal["HYPOTHESIS_STATUS_CHANGED"] = "HYPOTHESIS_STATUS_CHANGED"
    hypothesis_id: UUID
    before: HypothesisStatus
    after: HypothesisStatus

    @model_validator(mode="after")
    def require_change(self) -> "HypothesisStatusChangedPayload":
        if self.before is self.after:
            raise ValueError("hypothesis status change requires different before and after statuses")
        return self


class RetrievalCompletedPayload(_VersionedPayload):
    kind: Literal["RETRIEVAL_COMPLETED"] = "RETRIEVAL_COMPLETED"
    query_id: UUID
    hypothesis_id: UUID
    query: EvidenceSearchQuery
    results: tuple[EvidenceSearchResult, ...]
    model_run_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
    questions: tuple[UnresolvedQuestion, ...] = Field(default=(), exclude_if=lambda value: not value)

    @field_validator("questions")
    @classmethod
    def order_questions(cls, value: tuple[UnresolvedQuestion, ...]) -> tuple[UnresolvedQuestion, ...]:
        return HypothesisCreatedPayload.order_questions(value)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: EvidenceSearchQuery) -> EvidenceSearchQuery:
        return EvidenceSearchQuery.model_validate(_event_data(value))

    @field_validator("results")
    @classmethod
    def require_ranked_results(
        cls, value: tuple[EvidenceSearchResult, ...],
    ) -> tuple[EvidenceSearchResult, ...]:
        value = tuple(EvidenceSearchResult.model_validate(_event_data(result)) for result in value)
        _ordered_ids(tuple(result.evidence_id for result in value))
        if tuple(result.rank for result in value) != tuple(range(1, len(value) + 1)):
            raise ValueError("retrieval results must have contiguous ranks in rank order")
        return tuple(result.model_copy(update={
            "matched_filters": _ordered_text(result.matched_filters),
        }) for result in value)

    @model_validator(mode="after")
    def require_result_limit(self) -> "RetrievalCompletedPayload":
        if len(self.results) > self.query.limit:
            raise ValueError("retrieval results exceed query limit")
        if any(
            q.hypothesis_id != self.hypothesis_id or q.investigation_id != self.query.investigation_id
            or q.case_id != self.query.case_id for q in self.questions
        ):
            raise ValueError("retrieval question context mismatch")
        return self


class InvestigationCompletedPayload(_VersionedPayload):
    kind: Literal["INVESTIGATION_COMPLETED"] = "INVESTIGATION_COMPLETED"
    projection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


EventPayload = Annotated[
    InvestigationStartedPayload
    | StageTransitionPayload
    | EntityCreatedPayload
    | TimelineCreatedPayload
    | ClaimCreatedPayload
    | HypothesisCreatedPayload
    | CritiqueCreatedPayload
    | TestCreatedPayload
    | TestResolvedPayload
    | ConfidenceRevisedPayload
    | HypothesisStatusChangedPayload
    | RetrievalCompletedPayload
    | InvestigationCompletedPayload,
    Field(discriminator="kind"),
]


class InvestigationEvent(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    sequence: int = Field(ge=1)
    event_type: EventType
    target_type: EventTargetType
    target_id: UUID
    payload: EventPayload
    model_run_id: UUID | None = None
    previous_event_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash_algorithm: Literal["postgres-investigation-event-v1"]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: EventPayload) -> EventPayload:
        return type(value).model_validate(_event_data(value))

    @model_validator(mode="after")
    def require_chain_and_payload(self) -> "InvestigationEvent":
        if self.sequence == 1 and self.previous_event_hash is not None:
            raise ValueError("first event previous_event_hash must be null")
        if self.sequence > 1 and self.previous_event_hash is None:
            raise ValueError("previous_event_hash is required after sequence one")
        payload = self.payload
        if self.event_type != payload.kind:
            raise ValueError("event_type must match payload kind")
        if self.target_type != _TARGET_TYPES[self.event_type]:
            raise ValueError("target_type must match event_type")
        expected_model_run_id = None
        if isinstance(payload, (
            EntityCreatedPayload, TimelineCreatedPayload, ClaimCreatedPayload,
            HypothesisCreatedPayload, CritiqueCreatedPayload, TestCreatedPayload,
            TestResolvedPayload, ConfidenceRevisedPayload,
        )):
            if (
                payload.record.investigation_id != self.investigation_id
                or payload.record.case_id != self.case_id
            ):
                raise ValueError("record context must match event investigation and case")
            expected_target_id = payload.record.id
            if not isinstance(payload, ConfidenceRevisedPayload):
                expected_model_run_id = payload.record.model_run_id
        elif isinstance(payload, RetrievalCompletedPayload):
            if (
                payload.query.investigation_id != self.investigation_id
                or payload.query.case_id != self.case_id
            ):
                raise ValueError("query context must match event investigation and case")
            expected_target_id = payload.query_id
            expected_model_run_id = payload.model_run_id
        elif isinstance(payload, HypothesisStatusChangedPayload):
            expected_target_id = payload.hypothesis_id
        else:
            expected_target_id = self.investigation_id
        if self.target_id != expected_target_id:
            raise ValueError("target_id must match payload record or investigation id")
        if self.model_run_id != expected_model_run_id:
            raise ValueError("event model_run_id must match payload model-run attribution")
        return self

    def hash_envelope(self) -> dict[str, JsonValue]:
        return {
            "previous_hash": self.previous_event_hash,
            "investigation_id": str(self.investigation_id),
            "case_id": str(self.case_id),
            "sequence": self.sequence,
            "event_type": self.event_type,
            "target_type": self.target_type,
            "target_id": str(self.target_id),
            "payload": self.payload.model_dump(mode="json"),
            "model_run_id": str(self.model_run_id) if self.model_run_id else None,
        }

    def computed_hash(self) -> str:
        return canonical_digest(self.hash_envelope())
