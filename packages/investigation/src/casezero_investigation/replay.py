from uuid import UUID

from casezero_evidence.models import StrictModel
from pydantic import Field

from casezero_investigation.events import (
    ClaimCreatedPayload,
    HypothesisCreatedPayload,
    InvestigationEvent,
    InvestigationStartedPayload,
    StageTransitionPayload,
)
from casezero_investigation.models import (
    Claim,
    Hypothesis,
    InvestigationStage,
    InvestigationStatus,
)


class ReplayError(ValueError):
    pass


class InvestigationProjection(StrictModel):
    investigation_id: UUID
    case_id: UUID
    status: InvestigationStatus
    current_stage: InvestigationStage
    configuration_hash: str
    claims: dict[UUID, Claim] = Field(default_factory=dict)
    hypotheses: dict[UUID, Hypothesis] = Field(default_factory=dict)
    event_head_hash: str


def replay(events: tuple[InvestigationEvent, ...]) -> InvestigationProjection:
    if not events:
        raise ReplayError("replay requires events")
    first = events[0]
    if first.sequence != 1 or not isinstance(first.payload, InvestigationStartedPayload):
        raise ReplayError("event sequence must start with investigation")
    if first.computed_hash() != first.event_hash:
        raise ReplayError("event digest does not match payload")
    projection = InvestigationProjection(
        investigation_id=first.investigation_id,
        case_id=first.case_id,
        status=first.payload.status,
        current_stage=first.payload.current_stage,
        configuration_hash=first.payload.configuration_hash,
        event_head_hash=first.event_hash,
    )
    previous = first
    for expected_sequence, event in enumerate(events[1:], start=2):
        if event.sequence != expected_sequence:
            raise ReplayError("event sequence is not contiguous")
        if event.previous_event_hash != previous.event_hash:
            raise ReplayError("event previous hash does not match")
        if event.computed_hash() != event.event_hash:
            raise ReplayError("event digest does not match payload")
        if (
            event.investigation_id != projection.investigation_id
            or event.case_id != projection.case_id
        ):
            raise ReplayError("event investigation context changed")
        projection = _apply(projection, event)
        previous = event
    return projection.model_copy(update={"event_head_hash": previous.event_hash})


def _apply(
    projection: InvestigationProjection, event: InvestigationEvent
) -> InvestigationProjection:
    payload = event.payload
    if isinstance(payload, ClaimCreatedPayload):
        if payload.record.id in projection.claims:
            raise ReplayError("duplicate claim creation")
        claims = dict(projection.claims)
        claims[payload.record.id] = payload.record
        return projection.model_copy(update={"claims": claims})
    if isinstance(payload, HypothesisCreatedPayload):
        if payload.record.id in projection.hypotheses:
            raise ReplayError("duplicate hypothesis creation")
        hypotheses = dict(projection.hypotheses)
        hypotheses[payload.record.id] = payload.record
        return projection.model_copy(update={"hypotheses": hypotheses})
    if isinstance(payload, StageTransitionPayload):
        if payload.previous_stage is not projection.current_stage:
            raise ReplayError("stage transition starts from wrong stage")
        return projection.model_copy(update={"current_stage": payload.next_stage})
    raise ReplayError("event payload cannot be applied after start")
