import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, field_validator, model_validator

from casezero_evidence.models import StrictModel

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


class WorkflowStage(StrEnum):
    ACQUISITION = "ACQUISITION"
    PROCESSING = "PROCESSING"
    BLIND = "BLIND"
    EVALUATION = "EVALUATION"


class RuntimeActor(StrEnum):
    ACQUISITION = "ACQUISITION"
    PROCESSOR = "PROCESSOR"
    BLIND = "BLIND"
    EVALUATION = "EVALUATION"


class AccessCapability(StrEnum):
    NTSB_ACQUISITION = "NTSB_ACQUISITION"
    CONTEXT_DEV = "CONTEXT_DEV"
    WEB_SEARCH = "WEB_SEARCH"
    RAW_STORAGE = "RAW_STORAGE"
    SUPABASE_DATABASE = "SUPABASE_DATABASE"
    MODEL_INFERENCE = "MODEL_INFERENCE"
    EVIDENCE_READ = "EVIDENCE_READ"
    LOCK = "LOCK"


class AccessOperation(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    NETWORK = "NETWORK"
    LOCK = "LOCK"
    DENY = "DENY"


class AuditReasonCode(StrEnum):
    ALLOWED_ACQUISITION = "ALLOWED_ACQUISITION"
    ALLOWED_PROCESSING = "ALLOWED_PROCESSING"
    ALLOWED_EVIDENCE_READ = "ALLOWED_EVIDENCE_READ"
    ALLOWED_MODEL_HOST = "ALLOWED_MODEL_HOST"
    ALLOWED_DATABASE = "ALLOWED_DATABASE"
    BLOCKED_CAPABILITY = "BLOCKED_CAPABILITY"
    BLOCKED_DOCUMENT = "BLOCKED_DOCUMENT"
    EVALUATION_LOCK_REQUIRED = "EVALUATION_LOCK_REQUIRED"
    LOCK_CREATED = "LOCK_CREATED"
    ENTERED_BLIND = "ENTERED_BLIND"


class AssessmentSnapshot(StrictModel):
    schema_version: str = Field(min_length=1)
    assessment_kind: str = Field(min_length=1)
    payload: JsonValue


class InvestigationLock(StrictModel):
    case_id: UUID
    assessment_snapshot: AssessmentSnapshot
    assessment_hash: str = Field(pattern=_SHA256_PATTERN)
    evidence_set_hash: str = Field(pattern=_SHA256_PATTERN)
    hash_algorithm: Literal["postgres-jsonb-text-v1"]
    model_versions: dict[str, str] = Field(min_length=1)
    prompt_versions: dict[str, str] = Field(min_length=1)
    system_version: str = Field(min_length=1)
    locked_at: datetime

    @field_validator("model_versions", "prompt_versions")
    @classmethod
    def require_version_values(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key or not version for key, version in value.items()):
            raise ValueError("version names and values must be non-empty")
        return value

    @field_validator("locked_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be UTC-aware")
        return value


class AccessAuditEvent(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    case_id: UUID
    stage: WorkflowStage
    actor_role: RuntimeActor
    capability: AccessCapability
    operation: AccessOperation
    target_document_id: UUID | None = None
    network_host: str | None = None
    allowed: bool
    reason_code: AuditReasonCode
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be UTC-aware")
        return value

    @field_validator("network_host")
    @classmethod
    def require_hostname(cls, value: str | None) -> str | None:
        if value is not None and not _HOST_PATTERN.fullmatch(value):
            raise ValueError("network_host must be a lowercase hostname")
        return value

    @model_validator(mode="after")
    def require_operation_context(self) -> "AccessAuditEvent":
        expected_stage = {
            RuntimeActor.ACQUISITION: WorkflowStage.ACQUISITION,
            RuntimeActor.PROCESSOR: WorkflowStage.PROCESSING,
            RuntimeActor.BLIND: WorkflowStage.BLIND,
            RuntimeActor.EVALUATION: WorkflowStage.EVALUATION,
        }[self.actor_role]
        if self.stage is not expected_stage:
            raise ValueError("actor_role does not match workflow stage")
        if self.operation is AccessOperation.NETWORK and self.network_host is None:
            raise ValueError("network_host is required for network operations")
        if self.operation is not AccessOperation.NETWORK and self.network_host is not None:
            raise ValueError("network_host is only valid for network operations")
        if self.operation is AccessOperation.DENY and self.allowed:
            raise ValueError("denied operations cannot be allowed")
        if not self.allowed and self.operation is not AccessOperation.DENY:
            raise ValueError("blocked access must use the DENY operation")
        return self
