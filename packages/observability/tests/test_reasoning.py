from dataclasses import dataclass

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
