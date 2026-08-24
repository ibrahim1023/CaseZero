# AGENTS.md

## Mission

Build CaseZero as an evidence-first AI investigation system: blind retrospective
analysis of real NTSB aviation cases, evaluated against later official findings.
Read `product-spec.md` (local-only input — never stage or commit it) and the
relevant ADRs in `docs/decision-log/` before changing behavior. The tracked
foundation design lives in `docs/superpowers/specs/`.

## Non-negotiable boundaries

- Only deterministic code may acquire artifacts, hash them, normalize
  timestamps, enforce visibility, lock cases, or compute evaluation metrics.
- AI does semantic interpretation, hypotheses, falsification, causal reasoning.
  It never touches raw acquisition, checksums, or the visibility boundary.
- Not a RAG app. Reasoning operates over persisted typed state (EvidenceItems,
  Claims, Entities, TimelineEvents, Hypotheses, Critiques, CausalEdges,
  FinalAssessment). If these structures don't drive the reasoning, the change
  is wrong regardless of how well it demos.
- Temporal blindness is enforced architecturally. During blind investigation,
  code — not prompts — blocks `OFFICIAL_ANALYSIS` and `FINAL_FINDING`
  documents and all unrestricted web/Context.dev access. Leakage incidents
  must be 0; a leakage eval failure blocks merge.
- Every substantive claim references ≥1 evidence record with a working
  SourceLocator back to the original artifact (page/paragraph, row/column,
  image region, audio segment).
- OBSERVED / INFERRED / DISPUTED / UNKNOWN stay distinct. Inference never
  silently becomes fact. Abstention (`INSUFFICIENT_EVIDENCE`) is a valid result.
- A locked assessment is immutable. Lock records assessment hash, evidence-set
  hash, model + prompt versions, timestamp, system version.
- Default tests and evals make no live model, NTSB, Context.dev, ElevenLabs,
  or Hyperfusion calls. Live checks require explicit opt-in env flags and never
  run in ordinary CI.
- Source artifacts are immutable after ingestion (SHA-256). Never commit source
  artifacts; commit manifests + URLs + checksums + retrieval scripts.
- Use only material the NTSB has officially made public. Never seek, acquire,
  infer, or use confidential, leaked, restricted, sealed, unpublished, or
  otherwise protected evidence.
- Do not use the NTSB seal, logo, or protected branding without written
  permission, and never imply government endorsement, affiliation, approval,
  sponsorship, certification, or operation.
- Keep official material and AI-generated content in separate data fields and
  UI components. Official facts carry source links and plain-text attribution;
  generated content carries a persistent “AI-Generated — Not an Official
  Finding” or “CaseZero Experimental Hypothesis” label and must not resemble an
  official report or government notice.
- When third-party docket-material rights are unclear, link to the official
  docket instead of republishing. Never use such material for model training
  without confirmed rights.
- Do not assert that a living person or company caused an accident, acted
  unlawfully, was negligent, or bears liability. Preserve uncertainty and
  context; generated conclusions are provisional and require expert review.
- Public-facing functionality must include a process to report, correct, or
  remove inaccurate or harmful generated content. Run the public-communication
  checklist in README.md before publishing any demo, screenshot, example,
  metadata, social-preview card, marketing copy, or post.
- Obtain qualified legal review before any higher-risk use listed in README.md,
  including commercialization, private evidence, identifiable-person claims,
  substantial third-party reproduction, unclear-rights training, or expansion
  beyond clearly labeled research and education.
- Use aware UTC datetimes, strict Pydantic boundary models, and structured
  outputs validated against schemas. Never drive state by parsing model prose.

## Workflow

1. Work test-first: failing test, minimal change, passing focused test, then
   broader verification.
2. Commit in bits — one task, one commit. Each plan task (or a single
   coherent step within it) is committed separately, immediately after its
   tests pass and its verification loop is green. Never batch multiple tasks
   or features into one commit, never carry uncommitted work across tasks,
   and never commit code whose focused tests have not been run. If a diff
   grows to cover two tasks, split it before committing.
3. Preserve public contracts (evidence model, tool layer, benchmark CLI,
   scenario/manifest formats) or update their tests, ADRs, and docs in the
   same change.
4. Run the verification loop in `docs/development/verification-loops.md`
   before claiming completion.
5. After every phase passes its full verification gate, invoke the repository
   `explain-diff-html` skill on the complete phase comparison (phase-start
   commit through phase-end commit). Generate and inspect the required
   self-contained `/tmp/...html` artifact before marking the phase complete;
   report its exact path and any verification limitations. The explanation is
   mandatory even when the user does not ask for it explicitly.

Do not use Superpowers execution skills (subagent-driven-development,
executing-plans) to implement. Follow the tracked implementation plan in
`docs/superpowers/plans/`, repository tests, ADRs, and verification loops
directly.

## No AI slop

No speculative abstractions, duplicate wrappers, generic helper modules,
placeholder/TODO prose, fake data presented as real, needless dependencies,
comments that restate code, broad exception swallowing, invented API fields, or
generic dashboard styling. Prefer exact domain names, small focused files,
evidence-backed behavior, and deletion of code that serves no accepted
requirement. If a generated artifact cannot explain its consumer and test,
remove it.

## Commands

Use `uv run` for Python tools. Use exact test paths while iterating, then run
the full offline gate (`uv run pytest`). Live acquisition/investigation checks
require opt-in flags (see `docs/testing.md`) and must never run in ordinary CI.

## Key docs

- Architecture: `docs/architecture.md` · Decisions: `docs/decision-log/`
- Testing: `docs/testing.md` · Evals: `docs/evaluation.md` ·
  Observability: `docs/observability.md`
- Verification loops: `docs/development/verification-loops.md`
- Project skills: `.devin/skills/`
