from dataclasses import dataclass
from uuid import UUID

import pytest
from casezero_evidence import ProcessingDisposition
from casezero_observability.reasoning import (
    ModelFailure,
    ModelRouter,
    ModelRoutingDenied,
    ReasoningRequest,
    StructuredGeneration,
)
from pydantic import BaseModel


class Output(BaseModel):
    value: str


@dataclass
class FakeModel:
    name: str
    fail: bool = False
    calls: int = 0
    provider: str = "hyperfusion"

    async def generate(self, request: ReasoningRequest[Output]) -> StructuredGeneration[Output]:
        self.calls += 1
        if self.fail:
            raise ModelFailure("schema exhausted")
        return StructuredGeneration(
            output=Output(value=self.name),
            input_tokens=12,
            output_tokens=4,
            retry_count=1,
        )


class Recorder:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []

    async def record_model_run(self, **values: object) -> None:
        self.runs.append(values)


@pytest.mark.asyncio
async def test_ai_allowed_uses_hyperfusion() -> None:
    model = FakeModel("hyperfusion")
    result = await ModelRouter(model).generate(
        ReasoningRequest(stage="evidence", prompt="bounded", output_type=Output),
        ProcessingDisposition.AI_ALLOWED,
    )
    assert result.output.value == "hyperfusion"
    assert model.calls == 1


@pytest.mark.parametrize(
    "disposition",
    [ProcessingDisposition.LOCAL_ONLY, ProcessingDisposition.LINK_ONLY, ProcessingDisposition.EXCLUDED],
)
@pytest.mark.asyncio
async def test_non_ai_allowed_never_calls_model(disposition: ProcessingDisposition) -> None:
    model = FakeModel("hyperfusion")
    with pytest.raises(ModelRoutingDenied, match=disposition.value):
        await ModelRouter(model).generate(
            ReasoningRequest(stage="evidence", prompt="bounded", output_type=Output), disposition
        )
    assert model.calls == 0


@pytest.mark.asyncio
async def test_successful_run_records_provider_usage() -> None:
    case_id = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
    recorder = Recorder()
    router = ModelRouter(FakeModel("hyperfusion"), recorder=recorder)

    await router.generate(
        ReasoningRequest(
            stage="evidence",
            prompt="bounded",
            output_type=Output,
            case_id=case_id,
        ),
        ProcessingDisposition.AI_ALLOWED,
    )

    assert recorder.runs[0]["input_tokens"] == 12
    assert recorder.runs[0]["output_tokens"] == 4
    assert recorder.runs[0]["retry_count"] == 1


@pytest.mark.asyncio
async def test_failed_hyperfusion_run_is_recorded_without_provider_fallback() -> None:
    case_id = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
    recorder = Recorder()
    router = ModelRouter(FakeModel("hyperfusion", fail=True), recorder=recorder)
    request = ReasoningRequest(
        stage="evidence", prompt="bounded", output_type=Output, case_id=case_id
    )
    with pytest.raises(ModelFailure):
        await router.generate(request, ProcessingDisposition.AI_ALLOWED)
    assert [run["status"] for run in recorder.runs] == ["FAILED"]
    assert recorder.runs[0]["provider"] == "hyperfusion"
    assert recorder.runs[0]["prompt_hash"] == request.prompt_hash
