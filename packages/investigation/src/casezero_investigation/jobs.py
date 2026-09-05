from datetime import datetime
from enum import StrEnum
from typing import Literal, cast
from uuid import UUID, uuid4

from casezero_evidence.models import StrictModel
from pydantic import Field, JsonValue, field_validator, model_validator

from casezero_investigation.models import InvestigationStage, _require_utc


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AttemptOutcome(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    LEASE_EXPIRED = "LEASE_EXPIRED"


class FailureCode(StrEnum):
    PROVIDER_EXHAUSTED = "PROVIDER_EXHAUSTED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    REFERENCE_INVALID = "REFERENCE_INVALID"
    DIVERSITY_INVALID = "DIVERSITY_INVALID"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    REPLAY_MISMATCH = "REPLAY_MISMATCH"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"


class ModelRequestStatus(StrEnum):
    RESERVED = "RESERVED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    SCHEMA_FAILED = "SCHEMA_FAILED"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    LEASE_EXPIRED = "LEASE_EXPIRED"


class ValidationIssue(StrictModel):
    path: tuple[str, ...] = Field(min_length=1)
    code: str = Field(min_length=1)


class InvestigationConfig(StrictModel):
    stage_versions: dict[str, str] = Field(min_length=1)
    model_versions: dict[str, str] = Field(min_length=1)
    prompt_versions: dict[str, str] = Field(min_length=1)
    prompt_git_shas: dict[str, str] = Field(min_length=1)
    retrieval_version: str = Field(min_length=1)
    confidence_rule: Literal["weighted-delta-v1"]
    lease_seconds: Literal[1800] = 1800
    max_job_attempts: Literal[3] = 3
    max_model_requests: Literal[3] = 3

    @model_validator(mode="after")
    def require_matching_stage_keys(self) -> "InvestigationConfig":
        keys = set(self.stage_versions)
        if not all(
            set(values) == keys
            for values in (
                self.model_versions,
                self.prompt_versions,
                self.prompt_git_shas,
            )
        ):
            raise ValueError("stage configuration keys must match")
        if any(
            not key or not value
            for values in (
                self.stage_versions,
                self.model_versions,
                self.prompt_versions,
                self.prompt_git_shas,
            )
            for key, value in values.items()
        ):
            raise ValueError("stage configuration values must be non-empty")
        return self

    def canonical_payload(self) -> dict[str, JsonValue]:
        return {
            "stage_versions": dict(sorted(self.stage_versions.items())),
            "model_versions": dict(sorted(self.model_versions.items())),
            "prompt_versions": dict(sorted(self.prompt_versions.items())),
            "prompt_git_shas": dict(sorted(self.prompt_git_shas.items())),
            "retrieval_version": self.retrieval_version,
            "confidence_rule": self.confidence_rule,
            "lease_seconds": self.lease_seconds,
            "max_job_attempts": self.max_job_attempts,
            "max_model_requests": self.max_model_requests,
        }


class InvestigationJob(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    stage: InvestigationStage
    work_key: str = Field(min_length=1)
    input_state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: JobStatus
    attempt_count: int = Field(ge=0, le=3)
    model_request_count: int = Field(ge=0, le=3)
    retryable: bool
    failure_code: FailureCode | None = None
    worker_id: str | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    @field_validator("claimed_at", "lease_expires_at", "completed_at", "created_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_status_context(self) -> "InvestigationJob":
        lease_values = (self.worker_id, self.claimed_at, self.lease_expires_at)
        if self.status is JobStatus.RUNNING:
            if any(value is None for value in lease_values):
                raise ValueError("running job requires lease context")
            if (
                self.lease_expires_at is not None
                and self.claimed_at is not None
                and self.lease_expires_at <= self.claimed_at
            ):
                raise ValueError("lease expiration must follow claim time")
        elif any(value is not None for value in lease_values):
            raise ValueError("only running jobs may retain lease context")
        if self.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
            if self.completed_at is None:
                raise ValueError("terminal job requires completed_at")
        elif self.completed_at is not None:
            raise ValueError("nonterminal job cannot have completed_at")
        return self


class InvestigationJobAttempt(StrictModel):
    job_id: UUID
    attempt_number: int = Field(ge=1, le=3)
    worker_id: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime | None = None
    outcome: AttemptOutcome
    failure_code: FailureCode | None = None
    model_request_count: int = Field(ge=0, le=3)

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_outcome_context(self) -> "InvestigationJobAttempt":
        if self.outcome is AttemptOutcome.RUNNING and self.completed_at is not None:
            raise ValueError("running attempt cannot have completed_at")
        if self.outcome is not AttemptOutcome.RUNNING and self.completed_at is None:
            raise ValueError("terminal attempt requires completed_at")
        return self


class ModelRequestAttempt(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    job_attempt_number: int = Field(ge=1, le=3)
    request_ordinal: int = Field(ge=1, le=3)
    status: ModelRequestStatus
    failure_code: FailureCode | None = None
    validation_issues: tuple[ValidationIssue, ...] = ()
    started_at: datetime
    completed_at: datetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)

    @model_validator(mode="after")
    def require_request_context(self) -> "ModelRequestAttempt":
        terminal = {
            ModelRequestStatus.SUCCEEDED,
            ModelRequestStatus.SCHEMA_FAILED,
            ModelRequestStatus.PROVIDER_FAILED,
            ModelRequestStatus.LEASE_EXPIRED,
        }
        if self.status in terminal and self.completed_at is None:
            raise ValueError("terminal model request requires completed_at")
        if self.status not in terminal and self.completed_at is not None:
            raise ValueError("open model request cannot have completed_at")
        if self.status is not ModelRequestStatus.SCHEMA_FAILED and self.validation_issues:
            raise ValueError("validation issues require schema failure")
        return self


def job_input_payload(
    *,
    stage: InvestigationStage,
    active_evidence_ids: tuple[UUID, ...],
    upstream_record_ids: tuple[UUID, ...],
    stage_version: str,
    prompt_hash: str,
    prompt_git_sha: str,
    model: str,
    retrieval_version: str,
    confidence_rule: str,
) -> dict[str, JsonValue]:
    return {
        "stage": stage.value,
        "active_evidence_ids": cast(
            list[JsonValue], sorted(str(value) for value in active_evidence_ids)
        ),
        "upstream_record_ids": cast(
            list[JsonValue], sorted(str(value) for value in upstream_record_ids)
        ),
        "stage_version": stage_version,
        "prompt_hash": prompt_hash,
        "prompt_git_sha": prompt_git_sha,
        "model": model,
        "retrieval_version": retrieval_version,
        "confidence_rule": confidence_rule,
    }
