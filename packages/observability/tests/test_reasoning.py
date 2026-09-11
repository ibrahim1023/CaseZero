import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from casezero_evidence import (
    AccessCapability,
    AccessOperation,
    ProcessingDisposition,
    RuntimeActor,
    WorkflowStage,
)
from casezero_observability.reasoning import (
    ModelAuditContext,
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
    provider: str = "groq"

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


class AccessRecorder:
    def __init__(self) -> None:
        self.events = []

    async def record(self, event) -> None:
        self.events.append(event)


@pytest.mark.parametrize("response_kind", ("provider_failure", "timeout", "schema_failure", "success"))
async def test_budgeted_generation_uses_one_http_request_and_closes_its_client(response_kind) -> None:
    import json

    import httpx
    import respx
    from casezero_observability.reasoning import PydanticReasoningModel

    def respond(request):
        if response_kind == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if response_kind == "provider_failure":
            return httpx.Response(503, json={"error": {"message": "fixture unavailable"}})
        body = json.loads(request.content)
        output_tool = body["tools"][0]["function"]["name"]
        return httpx.Response(200, json={
            "id": "fixture-response", "object": "chat.completion", "created": 0,
            "model": "qwen/qwen3-32b", "choices": [{
                "index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{
                        "id": "fixture-output", "type": "function", "function": {
                            "name": output_tool,
                            "arguments": "{}" if response_kind == "schema_failure" else '{"value":"measured"}',
                        },
                    }],
                },
            }], "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        })

    with respx.mock as http:
        route = http.post("https://model.example.test/v1/chat/completions").mock(side_effect=respond)
        model = PydanticReasoningModel.openai_compatible(
            "qwen/qwen3-32b", "https://model.example.test/v1", "fixture-key",
            provider="groq", single_request=True,
        )
        request = ReasoningRequest(stage="hypotheses", prompt="synthetic input", prompt_template="test.v1", output_type=Output)
        if response_kind == "success":
            result = await model.generate(request)
            assert result.output.value == "measured"
            assert (result.input_tokens, result.output_tokens, result.retry_count) == (12, 4, 0)
        else:
            with pytest.raises(ModelFailure):
                await model.generate(request)
        assert route.call_count == 1
        assert model._model.client.is_closed()
        if response_kind == "success":
            assert (await model.generate(request)).output.value == "measured"
            assert route.call_count == 2
            assert model._model.client.is_closed()


async def test_generation_paces_consecutive_provider_requests(monkeypatch) -> None:
    import httpx
    import respx
    from casezero_observability import reasoning

    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(reasoning.asyncio, "sleep", sleep)
    response = {
        "id": "fixture-response", "object": "chat.completion", "created": 0,
        "model": "fixture", "choices": [{
            "index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "fixture-output", "type": "function", "function": {
                        "name": "final_result", "arguments": '{"value":"measured"}',
                    },
                }],
            },
        }], "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }
    with respx.mock as http:
        http.post("https://model.example.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=response)
        )
        model = reasoning.PydanticReasoningModel.openai_compatible(
            "fixture", "https://model.example.test/v1", "fixture-key",
            single_request=True, minimum_request_interval_seconds=60,
        )
        request = ReasoningRequest(
            stage="test", prompt="input", prompt_template="test.v1", output_type=Output,
        )
        await model.generate(request)
        await model.generate(request)

    assert len(waits) == 1
    assert 59 < waits[0] <= 60


def test_prompt_hash_uses_versioned_template_not_source_payload() -> None:
    request = ReasoningRequest(
        stage="evidence",
        prompt="private source payload",
        prompt_template="casezero.evidence.v1",
        output_type=Output,
    )

    assert request.prompt_hash == hashlib.sha256(b"casezero.evidence.v1").hexdigest()
    assert request.prompt_hash != hashlib.sha256(request.prompt.encode()).hexdigest()


@pytest.mark.asyncio
async def test_ai_allowed_uses_groq() -> None:
    model = FakeModel("groq")
    result = await ModelRouter(model).generate(
        ReasoningRequest(stage="evidence", prompt="bounded", prompt_template="test.v1", output_type=Output),
        ProcessingDisposition.AI_ALLOWED,
    )
    assert result.output.value == "groq"
    assert model.calls == 1


@pytest.mark.parametrize(
    "disposition",
    [ProcessingDisposition.LOCAL_ONLY, ProcessingDisposition.LINK_ONLY, ProcessingDisposition.EXCLUDED],
)
@pytest.mark.asyncio
async def test_non_ai_allowed_never_calls_model(disposition: ProcessingDisposition) -> None:
    model = FakeModel("groq")
    with pytest.raises(ModelRoutingDenied, match=disposition.value):
        await ModelRouter(model).generate(
            ReasoningRequest(stage="evidence", prompt="bounded", prompt_template="test.v1", output_type=Output), disposition
        )
    assert model.calls == 0


@pytest.mark.asyncio
async def test_model_call_records_hostname_only_access_event() -> None:
    case_id = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
    access = AccessRecorder()
    audit = ModelAuditContext(
        recorder=access,
        stage=WorkflowStage.PROCESSING,
        actor_role=RuntimeActor.PROCESSOR,
        network_host="api.groq.com",
        now=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )
    router = ModelRouter(FakeModel("groq"), audit=audit)

    await router.generate(
        ReasoningRequest(
            stage="evidence",
            prompt="private source payload",
            prompt_template="test.v1",
            output_type=Output,
            case_id=case_id,
        ),
        ProcessingDisposition.AI_ALLOWED,
    )

    event = access.events[0]
    assert event.capability is AccessCapability.MODEL_INFERENCE
    assert event.operation is AccessOperation.NETWORK
    assert event.network_host == "api.groq.com"
    assert "private source payload" not in event.model_dump_json()


@pytest.mark.asyncio
async def test_successful_run_records_provider_usage() -> None:
    case_id = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
    recorder = Recorder()
    router = ModelRouter(FakeModel("groq"), recorder=recorder)

    await router.generate(
        ReasoningRequest(
            stage="evidence",
            prompt="bounded",
            prompt_template="test.v1",
            output_type=Output,
            case_id=case_id,
        ),
        ProcessingDisposition.AI_ALLOWED,
    )

    assert recorder.runs[0]["input_tokens"] == 12
    assert recorder.runs[0]["output_tokens"] == 4
    assert recorder.runs[0]["retry_count"] == 1


@pytest.mark.asyncio
async def test_failed_groq_run_is_recorded_without_provider_fallback() -> None:
    case_id = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
    recorder = Recorder()
    router = ModelRouter(FakeModel("groq", fail=True), recorder=recorder)
    request = ReasoningRequest(
        stage="evidence", prompt="bounded", prompt_template="test.v1", output_type=Output, case_id=case_id
    )
    with pytest.raises(ModelFailure):
        await router.generate(request, ProcessingDisposition.AI_ALLOWED)
    assert [run["status"] for run in recorder.runs] == ["FAILED"]
    assert recorder.runs[0]["provider"] == "groq"
    assert recorder.runs[0]["prompt_hash"] == request.prompt_hash
