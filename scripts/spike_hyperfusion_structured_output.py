import argparse
import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

DEFAULT_MODELS = ("openai/gpt-oss-120b", "qwen/qwen3-32b")


class SpikeModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class EvidenceExtraction(SpikeModel):
    observation: str
    status: Literal["OBSERVED", "INFERRED"]
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ClaimExtraction(SpikeModel):
    text: str
    status: Literal["OBSERVED", "INFERRED", "DISPUTED", "UNKNOWN"]
    supporting_evidence_ids: list[str] = Field(min_length=1)
    contradicting_evidence_ids: list[str]
    confidence: float = Field(ge=0, le=1)


class Hypothesis(SpikeModel):
    title: str
    description: str
    supporting_claim_ids: list[str]
    contradicting_claim_ids: list[str]
    unresolved_questions: list[str]
    confidence: float = Field(ge=0, le=1)


class HypothesisSet(SpikeModel):
    hypotheses: list[Hypothesis] = Field(min_length=3, max_length=3)


class HypothesisCritique(SpikeModel):
    hypothesis_id: str
    strongest_contradiction: str
    missing_evidence: list[str]
    alternative_explanation: str
    critique_confidence: float = Field(ge=0, le=1)


SHAPES: dict[str, tuple[type[SpikeModel], str]] = {
    "evidence_extraction": (
        EvidenceExtraction,
        (
            "Extract one evidence-bound observation from: EVID-014, page 17: "
            "The left rudder cable was found fractured during postaccident examination. "
            "Do not claim when the fracture occurred."
        ),
    ),
    "claim_extraction": (
        ClaimExtraction,
        (
            "Create one claim from EVID-014: The left rudder cable was found fractured "
            "during postaccident examination. Separate observation from causal inference."
        ),
    ),
    "hypothesis_generation": (
        HypothesisSet,
        (
            "Generate exactly three competing hypotheses from claims C1: loss of directional "
            "control during rollout; C2: left rudder cable found fractured after impact; "
            "C3: crosswind from west-southwest. Include contradictions and unknowns; do not "
            "assume postaccident condition proves preimpact failure."
        ),
    ),
    "hypothesis_critique": (
        HypothesisCritique,
        (
            "Critique hypothesis H1: preimpact rudder cable failure caused loss of directional "
            "control. Evidence: E14 cable found fractured postaccident; E22 pilot reported left "
            "rudder input ineffective; E31 impact damage could have fractured the cable. Identify "
            "the strongest contradiction, missing evidence, and an alternative explanation."
        ),
    ),
}


async def run_once(
    semaphore: asyncio.Semaphore,
    agent: Agent,
    model_name: str,
    shape_name: str,
    prompt: str,
    run_number: int,
) -> dict[str, object]:
    async with semaphore:
        started = time.perf_counter()
        try:
            await agent.run(prompt)
        except UnexpectedModelBehavior as error:
            status = "structured_output_failure"
            failure_type = type(error).__name__
        except ModelAPIError as error:
            status = "provider_failure"
            failure_type = type(error).__name__
        else:
            status = "success"
            failure_type = None
        return {
            "model": model_name,
            "shape": shape_name,
            "run": run_number,
            "status": status,
            "failure_type": failure_type,
            "latency_seconds": round(time.perf_counter() - started, 3),
        }


async def run_spike(
    models: tuple[str, ...],
    runs: int,
    concurrency: int,
    output_path: Path,
    existing_records: list[dict[str, object]],
) -> dict[str, object]:
    api_key = os.environ.get("HYPERFUSION_API_KEY")
    base_url = os.environ.get("HYPERFUSION_BASE_URL", "https://api.hyperfusion.io/v1")
    if not api_key:
        raise RuntimeError("HYPERFUSION_API_KEY is required")

    completed = {
        (str(record["model"]), str(record["shape"]), int(record["run"]))
        for record in existing_records
    }
    records = list(existing_records)
    semaphore = asyncio.Semaphore(concurrency)
    jobs = []
    for model_name in models:
        model = OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=base_url, api_key=api_key),
        )
        for shape_name, (output_type, prompt) in SHAPES.items():
            agent = Agent(
                model,
                output_type=output_type,
                instructions=(
                    "Return only evidence-grounded structured state. Never invent source IDs."
                ),
                retries=2,
            )
            jobs.extend(
                run_once(semaphore, agent, model_name, shape_name, prompt, run_number)
                for run_number in range(1, runs + 1)
                if (model_name, shape_name, run_number) not in completed
            )

    total = len(models) * len(SHAPES) * runs
    for completed_job in asyncio.as_completed(jobs):
        records.append(await completed_job)
        _write_checkpoint(output_path, models, runs, concurrency, records)
        print(f"completed {len(records)}/{total}", flush=True)

    summaries: list[dict[str, object]] = []
    for model_name in models:
        for shape_name in SHAPES:
            subset = [
                record
                for record in records
                if record["model"] == model_name and record["shape"] == shape_name
            ]
            statuses = Counter(str(record["status"]) for record in subset)
            summaries.append(
                {
                    "model": model_name,
                    "shape": shape_name,
                    "runs": len(subset),
                    "successes": statuses["success"],
                    "structured_output_failures": statuses["structured_output_failure"],
                    "provider_failures": statuses["provider_failure"],
                    "success_rate": round(statuses["success"] / len(subset), 4),
                    "mean_latency_seconds": round(
                        sum(float(record["latency_seconds"]) for record in subset) / len(subset),
                        3,
                    ),
                }
            )
    return {
        "runs_per_shape": runs,
        "concurrency": concurrency,
        "models": list(models),
        "summaries": summaries,
        "records": records,
    }


def _write_checkpoint(
    output_path: Path,
    models: tuple[str, ...],
    runs: int,
    concurrency: int,
    records: list[dict[str, object]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "runs_per_shape": runs,
                "concurrency": concurrency,
                "models": list(models),
                "records": records,
            },
            indent=2,
        )
        + "\n"
    )


def _load_checkpoint(
    output_path: Path, models: tuple[str, ...], runs: int
) -> list[dict[str, object]]:
    if not output_path.exists():
        return []
    payload = json.loads(output_path.read_text())
    if payload.get("runs_per_shape") != runs or payload.get("models") != list(models):
        raise RuntimeError("existing spike checkpoint does not match requested models and runs")
    records = payload.get("records")
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise RuntimeError("existing spike checkpoint has invalid records")
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark/results/hyperfusion-structured-output.json"),
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    models = tuple(args.models)
    existing_records = _load_checkpoint(args.output, models, args.runs)
    report = asyncio.run(
        run_spike(models, args.runs, args.concurrency, args.output, existing_records)
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for summary in report["summaries"]:
        print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
