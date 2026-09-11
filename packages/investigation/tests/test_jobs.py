from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from casezero_investigation import (
    AttemptOutcome,
    FailureCode,
    InvestigationConfig,
    InvestigationJob,
    InvestigationJobAttempt,
    InvestigationStage,
    JobStatus,
    ModelRequestAttempt,
    ModelRequestStatus,
    ValidationIssue,
    job_input_payload,
)
from casezero_investigation.repository import CandidateBatch
from casezero_investigation.worker import _promotion_prompt_data
from pydantic import ValidationError

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def config() -> InvestigationConfig:
    return InvestigationConfig(
        stage_versions={"PROMOTE_TIMELINE": "1.0.0"},
        model_versions={"PROMOTE_TIMELINE": "qwen/qwen3-32b"},
        prompt_versions={"PROMOTE_TIMELINE": "casezero.timeline.v1"},
        prompt_git_shas={"PROMOTE_TIMELINE": "deadbeef"},
        retrieval_version="fts-v1",
        confidence_rule="weighted-delta-v1",
    )


def test_promotion_prompt_contains_only_candidates_and_active_evidence_ids() -> None:
    evidence_ids = (UUID(int=2), UUID(int=1))

    assert _promotion_prompt_data(CandidateBatch(), evidence_ids) == {
        "candidates": CandidateBatch().model_dump(mode="json"),
        "active_evidence_ids": [str(UUID(int=1)), str(UUID(int=2))],
    }


def test_config_payload_contains_fixed_execution_limits() -> None:
    payload = config().canonical_payload()
    assert payload["lease_seconds"] == 1800
    assert payload["max_job_attempts"] == 3
    assert payload["max_model_requests"] == 3


def test_job_input_payload_sorts_set_like_ids() -> None:
    payload = job_input_payload(
        stage=InvestigationStage.GENERATE_HYPOTHESES,
        active_evidence_ids=(UUID(int=2), UUID(int=1)),
        upstream_record_ids=(UUID(int=4), UUID(int=3)),
        stage_version="1.0.0",
        prompt_hash="a" * 64,
        prompt_git_sha="deadbeef",
        model="qwen/qwen3-32b",
        retrieval_version="fts-v1",
        confidence_rule="weighted-delta-v1",
    )
    assert payload["active_evidence_ids"] == [str(UUID(int=1)), str(UUID(int=2))]
    assert payload["upstream_record_ids"] == [str(UUID(int=3)), str(UUID(int=4))]


def test_running_job_and_attempt_require_lease_context() -> None:
    job_id = uuid4()
    investigation_id = uuid4()
    job = InvestigationJob(
        id=job_id,
        investigation_id=investigation_id,
        stage=InvestigationStage.PROMOTE_TIMELINE,
        work_key="case",
        input_state_hash="a" * 64,
        status=JobStatus.RUNNING,
        attempt_count=1,
        model_request_count=0,
        retryable=True,
        worker_id="worker-1",
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=1800),
        created_at=NOW,
    )
    attempt = InvestigationJobAttempt(
        job_id=job_id,
        attempt_number=1,
        worker_id="worker-1",
        started_at=NOW,
        outcome=AttemptOutcome.RUNNING,
        model_request_count=0,
    )
    assert job.lease_expires_at > job.claimed_at
    assert attempt.outcome is AttemptOutcome.RUNNING

    with pytest.raises(ValidationError, match="lease"):
        InvestigationJob.model_validate(
            job.model_dump() | {"worker_id": None, "lease_expires_at": None}
        )


def test_model_request_attempt_keeps_only_sanitized_validation_issues() -> None:
    request = ModelRequestAttempt(
        job_id=uuid4(),
        job_attempt_number=1,
        request_ordinal=1,
        status=ModelRequestStatus.SCHEMA_FAILED,
        failure_code=FailureCode.SCHEMA_INVALID,
        validation_issues=(ValidationIssue(path=("hypotheses", "0", "title"), code="missing"),),
        started_at=NOW,
        completed_at=NOW,
    )
    assert set(type(request).model_fields) == {
        "id",
        "job_id",
        "job_attempt_number",
        "request_ordinal",
        "status",
        "failure_code",
        "validation_issues",
        "started_at",
        "completed_at",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        ModelRequestAttempt.model_validate(
            request.model_dump() | {"rejected_value": "private model output"}
        )
