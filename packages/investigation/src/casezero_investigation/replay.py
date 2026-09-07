from datetime import datetime
from decimal import Decimal
from uuid import UUID

from casezero_evidence.models import StrictModel
from pydantic import Field, JsonValue, ValidationError

from casezero_investigation.canonical import canonical_digest, postgres_jsonb_text
from casezero_investigation.confidence import assign_hypothesis_statuses, test_delta
from casezero_investigation.events import (
    ClaimCreatedPayload,
    ConfidenceRevisedPayload,
    CritiqueCreatedPayload,
    EntityCreatedPayload,
    HypothesisCreatedPayload,
    HypothesisStatusChangedPayload,
    InvestigationCompletedPayload,
    InvestigationEvent,
    InvestigationStartedPayload,
    RetrievalCompletedPayload,
    StageTransitionPayload,
    TestCreatedPayload,
    TestResolvedPayload,
    TimelineCreatedPayload,
    _event_data,
)
from casezero_investigation.hypotheses import (
    ConfidenceRevision,
    HypothesisCritique,
    HypothesisTest,
    HypothesisTestDraft,
    HypothesisTestStatus,
    TemporalConsistencyParameters,
)
from casezero_investigation.models import (
    Claim,
    Hypothesis,
    InvestigationEntity,
    InvestigationStage,
    InvestigationStatus,
    TimelineEvent,
    UnresolvedQuestion,
)


class ReplayError(ValueError):
    pass


class HypothesisClaimLinks(StrictModel):
    supporting: tuple[UUID, ...]
    contradicting: tuple[UUID, ...]


class InvestigationProjection(StrictModel):
    investigation_id: UUID
    case_id: UUID
    status: InvestigationStatus
    current_stage: InvestigationStage
    configuration_hash: str
    entities: dict[UUID, InvestigationEntity] = Field(default_factory=dict)
    timeline: dict[UUID, TimelineEvent] = Field(default_factory=dict)
    claims: dict[UUID, Claim] = Field(default_factory=dict)
    hypotheses: dict[UUID, Hypothesis] = Field(default_factory=dict)
    hypothesis_claim_links: dict[UUID, HypothesisClaimLinks] = Field(default_factory=dict)
    questions: dict[UUID, UnresolvedQuestion] = Field(default_factory=dict)
    critiques: dict[UUID, HypothesisCritique] = Field(default_factory=dict)
    tests: dict[UUID, HypothesisTest] = Field(default_factory=dict)
    revisions: dict[UUID, ConfidenceRevision] = Field(default_factory=dict)
    retrievals: dict[UUID, RetrievalCompletedPayload] = Field(default_factory=dict)
    event_head_hash: str

    def canonical_state(self) -> dict[str, JsonValue]:
        state = self.model_dump(exclude={
            "status", "current_stage", "configuration_hash", "event_head_hash",
        })
        state["retrievals"] = {
            identifier: payload.model_dump(exclude={"kind", "schema_version", "questions"})
            for identifier, payload in self.retrievals.items()
        }
        return {key: _canonical_value(value, key) for key, value in state.items()}


def _canonical_value(value: object, field: str) -> JsonValue:
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item, str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if key not in {"created_at", "started_at", "completed_at"}
        }
    if isinstance(value, (tuple, list)):
        items = [_canonical_value(item, field) for item in value]
        if field not in {"results", "test_deltas"}:
            items.sort(key=lambda item: (
                " ".join(item.split()).casefold() if isinstance(item, str)
                else postgres_jsonb_text(item)
            ))
        return items
    if isinstance(value, Decimal):
        if value.is_zero():
            return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("unsupported canonical investigation value")


def replay(events: tuple[InvestigationEvent, ...]) -> InvestigationProjection:
    if not events:
        raise ReplayError("replay requires events")
    projection: InvestigationProjection | None = None
    previous_hash: str | None = None
    event_ids: set[UUID] = set()
    for sequence, candidate in enumerate(events, start=1):
        try:
            event = InvestigationEvent.model_validate(_event_data(candidate))
        except ValidationError:
            raise ReplayError("invalid event payload") from None
        if event.sequence != sequence:
            raise ReplayError("event sequence must start at one and be contiguous")
        if event.previous_event_hash != previous_hash:
            raise ReplayError("event previous hash does not match")
        if event.id in event_ids:
            raise ReplayError("duplicate event ID")
        try:
            digest = event.computed_hash()
        except ValueError as error:
            raise ReplayError("invalid event hash envelope") from error
        if digest != event.event_hash:
            raise ReplayError("event digest does not match payload")
        if projection is None:
            if not isinstance(event.payload, InvestigationStartedPayload):
                raise ReplayError("event sequence must start with investigation")
            projection = InvestigationProjection(
                investigation_id=event.investigation_id,
                case_id=event.case_id,
                status=event.payload.status,
                current_stage=event.payload.current_stage,
                configuration_hash=event.payload.configuration_hash,
                event_head_hash=event.event_hash,
            )
        else:
            if (
                event.investigation_id != projection.investigation_id
                or event.case_id != projection.case_id
            ):
                raise ReplayError("event investigation context changed")
            projection = _apply(projection, event)
        event_ids.add(event.id)
        previous_hash = event.event_hash
        projection = projection.model_copy(update={"event_head_hash": previous_hash})
    assert projection is not None
    return projection


def _created[T](records: dict[UUID, T], identifier: UUID, record: T, kind: str) -> dict[UUID, T]:
    if identifier in records:
        raise ReplayError(f"duplicate {kind} creation")
    return records | {identifier: record}


def _existing[T](records: dict[UUID, T], identifier: UUID, kind: str) -> T:
    if identifier not in records:
        raise ReplayError(f"unknown {kind} reference before creation")
    return records[identifier]


def _test_draft(test: HypothesisTest) -> HypothesisTestDraft:
    return HypothesisTestDraft.model_validate(
        test.model_dump(include=set(HypothesisTestDraft.model_fields)),
    )


def _require_test_references(projection: InvestigationProjection, test: HypothesisTestDraft) -> None:
    if not set(test.claim_ids) <= projection.claims.keys():
        raise ReplayError("test references unknown claim before creation")
    parameters = test.typed_parameters
    if isinstance(parameters, TemporalConsistencyParameters) and not {
        parameters.before_event_id, parameters.after_event_id,
    } <= projection.timeline.keys():
        raise ReplayError("test references unknown timeline event before creation")


def _require_revision(projection: InvestigationProjection, revision: ConfidenceRevision) -> None:
    hypothesis = _existing(projection.hypotheses, revision.hypothesis_id, "hypothesis")
    if any(item.hypothesis_id == hypothesis.id for item in projection.revisions.values()):
        raise ReplayError("duplicate hypothesis confidence revision cycle")
    if revision.before != hypothesis.current_confidence:
        raise ReplayError("revision before confidence does not match hypothesis")
    reasons: list[str] = []
    for item in revision.test_deltas:
        test = _existing(projection.tests, item.test_id, "test")
        if test.hypothesis_id != hypothesis.id:
            raise ReplayError("revision test belongs to another hypothesis")
        if test.status is not HypothesisTestStatus.SUCCEEDED or test.outcome is None:
            raise ReplayError("revision requires SUCCEEDED tests, not pending or failed tests")
        delta = test_delta(test.outcome, test.strength)
        if item.delta != delta:
            raise ReplayError("revision test delta does not match outcome and strength")
        reasons.append(f"{test.id}:{test.outcome.value}:{test.strength.value}:{delta:+.4f}")
    succeeded_ids = {
        test.id for test in projection.tests.values()
        if test.hypothesis_id == hypothesis.id and test.status is HypothesisTestStatus.SUCCEEDED
    }
    if {item.test_id for item in revision.test_deltas} != succeeded_ids:
        raise ReplayError("revision must include every SUCCEEDED hypothesis test")
    rationale = "; ".join(reasons) if reasons else "no SUCCEEDED tests"
    expected_rationale = (
        f"weighted-delta-v1; {rationale}; before={revision.before:.4f}; "
        f"delta={revision.delta:+.4f}; after={revision.after:.4f}; clamp=[0.0500,0.9500]"
    )
    if revision.rationale != expected_rationale:
        raise ReplayError("revision rationale does not match deterministic test outcomes")


def _apply(
    projection: InvestigationProjection, event: InvestigationEvent,
) -> InvestigationProjection:
    payload = event.payload
    if projection.status in (InvestigationStatus.SUCCEEDED, InvestigationStatus.FAILED):
        raise ReplayError("completed investigation is terminal")
    if projection.current_stage is InvestigationStage.COMPLETE and not isinstance(
        payload, InvestigationCompletedPayload,
    ):
        raise ReplayError("COMPLETE stage permits only investigation completion")
    if isinstance(payload, EntityCreatedPayload):
        return projection.model_copy(update={
            "entities": _created(projection.entities, payload.record.id, payload.record, "entity"),
        })
    if isinstance(payload, TimelineCreatedPayload):
        return projection.model_copy(update={
            "timeline": _created(projection.timeline, payload.record.id, payload.record, "timeline"),
        })
    if isinstance(payload, ClaimCreatedPayload):
        return projection.model_copy(update={
            "claims": _created(projection.claims, payload.record.id, payload.record, "claim"),
        })
    if isinstance(payload, HypothesisCreatedPayload):
        hypotheses = _created(projection.hypotheses, payload.record.id, payload.record, "hypothesis")
        if not {
            *payload.supporting_claim_ids, *payload.contradicting_claim_ids,
        } <= projection.claims.keys():
            raise ReplayError("hypothesis references unknown claim before creation")
        questions = dict(projection.questions)
        for question in payload.questions:
            questions = _created(questions, question.id, question, "question")
        return projection.model_copy(update={
            "hypotheses": hypotheses,
            "questions": questions,
            "hypothesis_claim_links": projection.hypothesis_claim_links | {
                payload.record.id: HypothesisClaimLinks(
                    supporting=payload.supporting_claim_ids,
                    contradicting=payload.contradicting_claim_ids,
                ),
            },
        })
    if isinstance(payload, CritiqueCreatedPayload):
        critiques = _created(projection.critiques, payload.record.id, payload.record, "critique")
        _existing(projection.hypotheses, payload.record.hypothesis_id, "hypothesis")
        for draft in payload.record.proposed_tests:
            _require_test_references(projection, draft)
        return projection.model_copy(update={"critiques": critiques})
    if isinstance(payload, TestCreatedPayload):
        tests = _created(projection.tests, payload.record.id, payload.record, "test")
        _existing(projection.hypotheses, payload.record.hypothesis_id, "hypothesis")
        critique = _existing(projection.critiques, payload.record.critique_id, "critique")
        if critique.hypothesis_id != payload.record.hypothesis_id:
            raise ReplayError("test and critique must reference the same hypothesis")
        _require_test_references(projection, payload.record)
        draft = _test_draft(payload.record)
        if draft not in critique.proposed_tests:
            raise ReplayError("created test must match a proposed critique test")
        if any(
            test.critique_id == critique.id and _test_draft(test) == draft
            for test in projection.tests.values()
        ):
            raise ReplayError("duplicate test for critique proposal")
        return projection.model_copy(update={"tests": tests})
    if isinstance(payload, TestResolvedPayload):
        pending = _existing(projection.tests, payload.record.id, "test")
        if pending.status is not HypothesisTestStatus.PENDING:
            raise ReplayError("only PENDING tests can be resolved")
        result_fields = {"status", "outcome", "completed_at", "model_run_id", "result_evidence_ids"}
        if pending.model_dump(exclude=result_fields) != payload.record.model_dump(exclude=result_fields):
            raise ReplayError("resolved test changed immutable pending fields")
        if pending.model_run_id is not None and pending.model_run_id != payload.record.model_run_id:
            raise ReplayError("resolved test changed existing model-run attribution")
        return projection.model_copy(update={
            "tests": projection.tests | {payload.record.id: payload.record},
        })
    if isinstance(payload, ConfidenceRevisedPayload):
        revisions = _created(projection.revisions, payload.record.id, payload.record, "revision")
        _require_revision(projection, payload.record)
        hypothesis = projection.hypotheses[payload.record.hypothesis_id]
        return projection.model_copy(update={
            "revisions": revisions,
            "hypotheses": projection.hypotheses | {
                hypothesis.id: hypothesis.model_copy(update={"current_confidence": payload.record.after}),
            },
        })
    if isinstance(payload, HypothesisStatusChangedPayload):
        hypothesis = _existing(projection.hypotheses, payload.hypothesis_id, "hypothesis")
        if payload.before is not hypothesis.status:
            raise ReplayError("hypothesis status before does not match")
        expected = next(
            item for item in assign_hypothesis_statuses(tuple(projection.hypotheses.values()))
            if item.id == hypothesis.id
        )
        if payload.after is not expected.status:
            raise ReplayError("hypothesis status does not match confidence assignment")
        return projection.model_copy(update={
            "hypotheses": projection.hypotheses | {hypothesis.id: expected},
        })
    if isinstance(payload, RetrievalCompletedPayload):
        retrievals = _created(projection.retrievals, payload.query_id, payload, "retrieval")
        _existing(projection.hypotheses, payload.hypothesis_id, "hypothesis")
        if not set(payload.query.entity_ids) <= projection.entities.keys():
            raise ReplayError("retrieval references unknown entity before creation")
        questions = projection.questions
        for question in payload.questions:
            questions = _created(questions, question.id, question, "question")
        return projection.model_copy(update={"retrievals": retrievals, "questions": questions})
    if isinstance(payload, StageTransitionPayload):
        if payload.previous_stage is not projection.current_stage:
            raise ReplayError("stage transition starts from wrong stage")
        if payload.previous_stage is payload.next_stage and projection.status is not InvestigationStatus.PENDING:
            raise ReplayError("initial stage start already occurred")
        return projection.model_copy(update={
            "current_stage": payload.next_stage, "status": InvestigationStatus.RUNNING,
        })
    if isinstance(payload, InvestigationCompletedPayload):
        if projection.current_stage is not InvestigationStage.COMPLETE:
            raise ReplayError("investigation completion requires COMPLETE stage")
        if payload.projection_hash != canonical_digest(projection.canonical_state()):
            raise ReplayError("completion projection hash does not match canonical state")
        return projection.model_copy(update={"status": InvestigationStatus.SUCCEEDED})
    raise ReplayError("event payload cannot be applied after start")
