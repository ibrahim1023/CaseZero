# CaseZero

An evidence-first AI investigation system. CaseZero ingests the public evidence
docket of a real NTSB aviation accident, reconstructs the investigation while
**blind to the official probable-cause finding**, locks its own assessment, then
evaluates itself against the finding the NTSB eventually published.

> CaseZero is an experimental AI evidence-analysis system using publicly
> released investigation material. It does not replace the NTSB, does not
> provide authoritative accident findings, and establishes neither safety
> certification nor legal liability.

## What it is

```text
real NTSB case → public docket → typed evidence → timeline / entities / claims
→ competing hypotheses → falsification → causal graph → locked blind assessment
→ official finding reveal → measured evaluation
```

The product is the **investigation state** — persisted `EvidenceItems`,
`Claims`, `Entities`, `TimelineEvents`, `Hypotheses`, `Critiques`,
`CausalEdges`, and a `FinalAssessment` — not a chatbot. Retrieval supports the
investigation; retrieval is not the investigation.

## Non-negotiable principles

1. **Real evidence only.** Real NTSB cases for the product; synthetic fixtures
   only in tests.
2. **AI is mandatory, but bounded.** AI does semantic interpretation, hypothesis
   generation, falsification, and causal reasoning. Deterministic code does
   acquisition, hashing, parsing, timestamps, visibility rules, locking, and
   evaluation math.
3. **Not a RAG app.** No chunk → embed → top-k → answer flow. Reasoning
   operates over typed, provenance-aware state.
4. **Temporal blindness is architectural.** `OFFICIAL_ANALYSIS` and
   `FINAL_FINDING` material is blocked by an access-control boundary, not by
   prompts. Final-answer leakage incidents must be 0.
5. **Every claim cites evidence.** Observation, inference, dispute, and
   unknown are distinct statuses. The system may abstain
   (`INSUFFICIENT_EVIDENCE`).
6. **Blind results are immutable.** Locking records assessment hash, model and
   prompt versions, and evidence-set hash before any reveal.

## Repository layout

```text
apps/
  api/            FastAPI backend (Python)
  web/            Investigation UI (Next.js — built in Phase 8, after the engine proves out)
packages/
  ntsb/           NTSB acquisition: CAROL, developer API, docket discovery, downloader
  ingestion/      Modality processors: PDF / CSV / XLSX / image / audio → EvidenceItems
  evidence/       Typed evidence, claims, entities, timeline models + provenance
  retrieval/      Hybrid retrieval over typed evidence (metadata + FTS + pgvector)
  investigation/  Stage machine: hypotheses, falsification, causal graph, assessment, lock
  evaluation/     Reveal, graders, benchmark metrics
  observability/  Model-run spans, prompt versions, cost, replay
benchmark/        cases.json, runner, graders, results, reports
fixtures/         real-cases/ manifests (URLs + SHA-256, never the artifacts themselves)
scripts/          acquisition / maintenance scripts
docs/             architecture, decision-log (ADRs), development, specs, plans
.devin/skills/    project skills for agentic contributors
```

## Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12+, FastAPI, Pydantic | Document/data/AI ecosystem; strict boundary models |
| Agent stages | Pydantic AI | Typed structured outputs + constrained tools, model-agnostic |
| Models | Hyperfusion (OpenAI-compatible, open-weight) | Predictable cost; vision + speech available on one API |
| Database | Supabase Postgres + pgvector | Relational state + JSONB + FTS + vectors in one place |
| Object storage | Supabase Storage (local FS in dev) | Immutable source artifacts |
| Jobs | Postgres queue (`SKIP LOCKED`) | No Redis/Kafka decoration; resumable workflows |
| Evals | DeepEval (pytest-native) + custom graders | CI-friendly, judge calibration against human labels |
| Observability | Spans in our own Postgres tables | Every claim traceable to model run + evidence; replay is a product feature |

See `docs/architecture.md` and `docs/decision-log/` for the reasoning and the
alternatives considered.

## Current status

**Phase 0 — case acquisition** (see `docs/superpowers/plans/`). No
investigation AI exists yet. The first milestone is the five-case blind
pipeline; the polished UI comes only after that gate passes.

## Development

Prerequisites: `uv`, `supabase` CLI, Node 20+ (Phase 8 only).

```bash
cp .env.example .env        # fill in keys
supabase start              # local Postgres + Storage
uv run pytest               # offline test suite — no live model or network calls
```

Rules for contributors (human or agentic) live in `AGENTS.md`. The verification
loop to run before claiming anything is done lives in
`docs/development/verification-loops.md`.

## Data sources & legal

NTSB-authored reports and docket contents are public domain as US Government
works; attribute "Courtesy: National Transportation Safety Board". Third-party
material inside dockets may carry its own copyright — such artifacts are never
redistributed from this repo; `fixtures/real-cases/` stores manifests, source
URLs, and checksums, and a retrieval script re-fetches originals.
