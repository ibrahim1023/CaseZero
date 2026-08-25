import argparse
import base64
import io
import json
import os
from pathlib import Path

import pypdfium2 as pdfium
from casezero_observability.vision_probe import run_vision_probe
from openai import OpenAI


def image_url(path: Path, page: int) -> str:
    pdf = pdfium.PdfDocument(path)
    image = pdf[page].render(scale=1.2).to_pil()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path("benchmark/results/vision-probe.json"))
    args = parser.parse_args()
    sources = [
        (Path("data/real-cases/cen22fa375/04.pdf"), 0, "EXAMINATION-PAGE-1"),
        (Path("data/real-cases/cen22fa375/05.pdf"), 0, "FIRE-PAGE-1"),
    ]
    providers = [
        (
            "hyperfusion",
            "google/gemma-4-31b-it",
            OpenAI(
                api_key=os.environ["HYPERFUSION_API_KEY"],
                base_url=os.environ["HYPERFUSION_BASE_URL"],
            ),
        ),
        (
            "ollama",
            "qwen2.5vl:7b",
            OpenAI(api_key="ollama", base_url="http://localhost:11434/v1"),
        ),
    ]
    records = []
    for provider, model, client in providers:
        for index in range(args.runs):
            path, page, source_id = sources[index % len(sources)]
            record = run_vision_probe(client, model, image_url(path, page), source_id)
            record.update(
                {"provider": provider, "model": model, "run": index + 1, "source_id": source_id}
            )
            records.append(record)
            print(provider, index + 1, record["status"], flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"runs": args.runs, "records": records}, indent=2) + "\n")


if __name__ == "__main__":
    main()
