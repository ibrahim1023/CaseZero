from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from casezero_evidence.models import StrictModel
from pydantic import Field, field_validator, model_validator

from casezero_investigation.models import (
    Claim,
    Hypothesis,
    InvestigationStage,
    InvestigationStatus,
    _require_utc,
)

EventType = Literal[
    "INVESTIGATION_STARTED",
    "STAGE_TRANSITION",
    "CLAIM_CREATED",
    "HYPOTHESIS_CREATED",
]


class InvestigationStartedPayload(StrictModel):
    kind: Literal["INVESTIGATION_STARTED"] = "INVESTIGATION_STARTED"
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_stage: InvestigationStage
    status: InvestigationStatus


class StageTransitionPayload(StrictModel):
    kind: Literal["STAGE_TRANSITION"] = "STAGE_TRANSITION"
    previous_stage: InvestigationStage
    next_stage: InvestigationStage


class ClaimCreatedPayload(StrictModel):
    kind: Literal["CLAIM_CREATED"] = "CLAIM_CREATED"
    record: Claim


class HypothesisCreatedPayload(StrictModel):
    kind: Literal["HYPOTHESIS_CREATED"] = "HYPOTHESIS_CREATED"
    record: Hypothesis


EventPayload = Annotated[
    InvestigationStartedPayload
    | StageTransitionPayload
    | ClaimCreatedPayload
    | HypothesisCreatedPayload,
    Field(discriminator="kind"),
]


class InvestigationEvent(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    case_id: UUID
    sequence: int = Field(ge=1)
    event_type: EventType
    target_type: str = Field(min_length=1)
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

    @model_validator(mode="after")
    def require_chain_and_payload(self) -> "InvestigationEvent":
        if self.sequence == 1 and self.previous_event_hash is not None:
            raise ValueError("first event previous_event_hash must be null")
        if self.sequence > 1 and self.previous_event_hash is None:
            raise ValueError("previous_event_hash is required after sequence one")
        if self.event_type != self.payload.kind:
            raise ValueError("event_type must match payload kind")
        if self.payload.kind == "INVESTIGATION_STARTED" and self.target_id != self.investigation_id:
            raise ValueError("investigation start target must match investigation id")
        return self
