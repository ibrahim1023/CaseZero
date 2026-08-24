# CaseZero

An evidence-first AI investigation system. CaseZero ingests the public evidence
docket of a real NTSB aviation accident, reconstructs the investigation while
**blind to the official probable-cause finding**, locks its own assessment, then
evaluates itself against the finding the NTSB eventually published.

> **Disclaimer:** CaseZero is an independent, experimental AI project. It is not affiliated with, endorsed by, sponsored by, or approved by the National Transportation Safety Board (NTSB) or any other government agency. Official NTSB material is identified and linked to its source. CaseZero-generated summaries, hypotheses, classifications, and other AI outputs are experimental, may be incomplete or incorrect, and are not official findings, legal conclusions, or determinations of cause, fault, negligence, or liability. Consult the original NTSB materials and qualified experts before relying on any output.

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
7. **Independent and experimental.** Official NTSB material and CaseZero AI
   output stay in separate fields and interface components. Generated analysis
   is persistently labeled and never presented as an official finding,
   government determination, or assignment of blame or liability.

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

## Legal and public-communication guardrails

These are project guardrails, not legal advice. Obtain qualified legal review
for higher-risk uses.

### Sources, rights, and attribution

- Use only material the NTSB has officially made public. Preserve direct source
  links and plain-text attribution such as “Source: National Transportation
  Safety Board” wherever practical.
- Never seek, acquire, infer, or use confidential, leaked, restricted, sealed,
  unpublished, or otherwise protected investigation material.
- Do not use the NTSB seal, logo, or protected branding without written
  permission. CaseZero’s name, interface, metadata, marketing, screenshots,
  social-preview cards, and posts must not imply government affiliation,
  endorsement, approval, sponsorship, certification, or operation.
- NTSB dockets can include material created by manufacturers, operators,
  witnesses, photographers, consultants, and other third parties. Public
  availability does not establish unrestricted reuse rights. When ownership or
  reuse rights are unclear, link to the official docket instead of
  republishing; do not use the material for model training without confirming
  the necessary rights. This repository stores manifests, source URLs, and
  checksums rather than redistributing docket artifacts.

### Official findings and AI output

- Official material and generated content use separate data fields and
  interface components. Official facts display attribution and direct links
  where practical.
- Generated analysis is persistently and visibly labeled, for example:
  **CaseZero Experimental Hypothesis** or **AI-Generated — Not an Official
  Finding**. Official conclusions use **Official NTSB Finding**. AI output must
  not be styled like an official report, government notice, seal, or
  determination.
- Preserve uncertainty, conflicting evidence, and important limitations.
  Describe model conclusions as provisional analysis requiring expert
  verification.
- Do not assert as fact that a living person or company caused an accident,
  acted unlawfully, was negligent, or bears legal liability. Attribute
  authoritative public conclusions accurately and in context; avoid unsupported
  accusations, sensational language, or definitive blame claims.
- Public deployments must provide a process for reporting, correcting, or
  removing inaccurate or harmful generated content.

### Publication checklist

Before publishing a demo, generated analysis, screenshot, example dataset,
metadata, marketing page, social-preview card, or post, confirm that:

- only officially public material is used;
- official sources are identified and linked;
- no NTSB seal, logo, or protected branding appears without permission;
- nothing implies NTSB or government endorsement or affiliation;
- official findings and AI hypotheses are visibly separated and labeled;
- third-party material is linked rather than republished when rights are
  unclear;
- no unsupported statement assigns cause, misconduct, negligence, fault, or
  liability; and
- uncertainty and the experimental nature of the output are immediately
  visible.

### Obtain qualified legal review before

- commercializing CaseZero, charging for access, licensing outputs, or using
  content in advertising;
- accepting private, confidential, leaked, unpublished, or user-submitted
  evidence;
- making detailed claims about identifiable people or companies;
- reproducing substantial third-party documents, photographs, diagrams,
  recordings, or datasets;
- training models on material whose copyright, privacy status, or permitted
  use is unclear; or
- expanding beyond research, education, and clearly labeled experimental
  analysis.
