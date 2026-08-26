from dataclasses import dataclass
from uuid import UUID

import pytest
from casezero_evidence import ProcessingDisposition
from casezero_observability.reasoning import (
    ModelFailure,
    ModelRouter,
    ModelRoutingDenied,
    ReasoningRequest,
)
from pydantic import BaseModel


class Output(BaseModel):
    value: str


@dataclass
class FakeModel:
    name: str
    fail: bool = False
    calls: int = 0

    async def generate(self, request: ReasoningRequest[Output]) -> Output:
        self.calls += 1
        if self.fail:
            raise ModelFailure("schema exhausted")
        return Output(value=self.name)


@pytest.mark.asyncio
async def test_ai_allowed_uses_primary_without_fallback() -> None:
    primary, fallback = FakeModel("hyperfusion"), FakeModel("ollama")
    result = await ModelRouter(primary, fallback).generate(
        ReasoningRequest(stage="evidence", prompt="bounded", output_type=Output),
        ProcessingDisposition.AI_ALLOWED,
    )
    assert result.output.value == "hyperfusion"
    assert (primary.calls, fallback.calls) == (1, 0)


@pytest.mark.asyncio
async def test_primary_failure_uses_ollama_fallback() -> None:
    primary, fallback = FakeModel("hyperfusion", fail=True), FakeModel("ollama")
    result = await ModelRouter(primary, fallback).generate(
        ReasoningRequest(stage="evidence", prompt="bounded", output_type=Output),
        ProcessingDisposition.AI_ALLOWED,
    )
    assert result.output.value == "ollama"
    assert result.fallback_used is True


@pytest.mark.asyncio
async def test_local_only_skips_external_primary_and_link_only_is_denied() -> None:
    primary, fallback = FakeModel("hyperfusion"), FakeModel("ollama")
    router = ModelRouter(primary, fallback)
    request = ReasoningRequest(stage="vision", prompt="bounded", output_type=Output)
    assert (await router.generate(request, ProcessingDisposition.LOCAL_ONLY)).output.value == "ollama"
    assert primary.calls == 0
    with pytest.raises(ModelRoutingDenied):
        await router.generate(request, ProcessingDisposition.LINK_ONLY)


class Recorder:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []

    async def record_model_run(self, **values: object) -> None:
        self.runs.append(values)


@pytest.mark.asyncio
async def test_router_persists_failed_primary_and_successful_fallback_model_runs() -> None:
    case_id = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
    unit_id = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")
    recorder = Recorder()
    router = ModelRouter(
        FakeModel("hyperfusion", fail=True),
        FakeModel("ollama"),
        recorder=recorder,
    )
    request = ReasoningRequest(
        stage="evidence",
        prompt="bounded",
        output_type=Output,
        case_id=case_id,
        structural_unit_ids=(unit_id,),
    )

    result = await router.generate(request, ProcessingDisposition.AI_ALLOWED)

    assert result.run_id == recorder.runs[1]["run_id"]
    assert [run["status"] for run in recorder.runs] == ["FAILED", "SUCCEEDED"]
    assert recorder.runs[1]["parent_run_id"] == recorder.runs[0]["run_id"]
    assert recorder.runs[0]["prompt_hash"] == request.prompt_hash
    assert recorder.runs[1]["structural_unit_ids"] == (unit_id,)
