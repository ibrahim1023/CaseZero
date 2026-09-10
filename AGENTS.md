# AGENTS.md

## Mission

Build CaseZero as an evidence-first AI investigation system: blind retrospective
analysis of real NTSB aviation cases, evaluated against later official findings.
Read `product-spec.md` (local-only input — never stage or commit it) and the
relevant ADRs in `docs/decision-log/` before changing behavior. The tracked
foundation design lives in `docs/superpowers/specs/`.

## Current MVP scope and precedence

The current scope supersedes conflicting MVP definitions and sequencing in
older product/foundation documents and implementation plans. The local-only
`task.md` tracks this scope; never stage, commit, or push it. The boundaries
below remain mandatory.

- Prove the investigation process, not answer generation: observed/inferred
  evidence, competing hypotheses, contradictions, falsification, explainable
  confidence changes, evidence-backed causal reasoning, and replayable state.
- First finish the minimum Phase 3 runtime needed for `CEN22FA375`, then inspect
  that real blind run at the Phase 3.5 kill/continue gate before deeper
  architecture or Phases 4–6. Reuse the Phase 3 exit run for this inspection;
  do not make another live call solely for phase bookkeeping.
- Continue only if the trace demonstrates genuine evidence-driven evolution.
  Cosmetic hypothesis variants, ignored contradictions, static/arbitrary
  confidence, unsupported narratives, or mere docket summarization require
  re-scoping or stopping, even when the final answer matches the NTSB.
- MVP is three completed, rights-reviewed cases with meaningfully different
  causal structures, run blind through causal graph, assessment, lock, reveal,
  measured evaluation, replay, and manual grounding review (Phase 6.5).
  High answer accuracy is not required; leakage must be zero.
- UI and five-case validation are post-MVP. A thin UI may begin only after the
  three-case gate passes and must expose the investigation process. Defer
  public deployment, portfolio polish, assistant/chat features, and broad SaaS
  functionality. Do not wait for UI or all benchmark cases to prove one loop.
- Keep the runtime specific to this investigation: no generalized agent
  framework, distributed execution, arbitrary agent composition, or speculative
  workflow infrastructure. Preserve required provenance, blindness, atomic
  checkpoints, idempotent retry, and crash-resume without generalizing them.
- Use metadata + FTS first; add pgvector only if the D2 recall probe demonstrates
  material benefit. Add modalities only when actual case evidence requires them.
- Implement only evaluation and regression checks that affect the MVP go/no-go
  decision or protect an actual MVP property. Defer broad judge ensembles,
  sophisticated calibration, large regression suites, and generalized evaluator
  infrastructure unless needed to establish credibility.
- Before adding a task, require a material contribution to the blind experiment;
  before infrastructure, a need in one of the next three cases; before an
  evaluator, an effect on the go/no-go decision; before UI, better inspection of
  the investigation. Otherwise defer it. Audit uncommitted work against these
  criteria rather than treating an older plan as authorization for more scope.

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
  or Groq calls. Live checks require explicit opt-in env flags and never
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
requirement. Treat unnecessary output as a defect: before commit, every added
file, abstraction, field, test, plan step, document, UI element, and report must
name a concrete accepted requirement and real consumer, and must have an
appropriate verification path. If it cannot, delete it rather than polishing
or documenting it. Never claim completeness from mocked behavior, passing unit
tests alone, or generated prose when the relevant real integration has not run.

## Commands

Use `uv run` for Python tools. Use exact test paths while iterating, then run
the full offline gate (`uv run pytest`). Live acquisition/investigation checks
require opt-in flags (see `docs/testing.md`) and must never run in ordinary CI.
Stop background commands, watchers, browser previews, and database sessions as
soon as they are no longer in use. After interruption or failure, verify that
no stale process or open transaction remains before retrying.

Do not leave the user without an update during long-running work. Before a
command, live call, database operation, or subagent likely to take more than a
couple of minutes, state what is running and what result is expected. While it
remains active, provide a concise progress update at least every five minutes
or whenever a meaningful checkpoint, failure, or blocker occurs. If a command
is silent or takes disproportionately long, inspect its process/database state,
report what it is doing, and stop or narrow it rather than repeatedly waiting
without communication.

## Key docs

- Architecture: `docs/architecture.md` · Decisions: `docs/decision-log/`
- Testing: `docs/testing.md` · Evals: `docs/evaluation.md` ·
  Observability: `docs/observability.md`
- Verification loops: `docs/development/verification-loops.md`
- Project skills: `.devin/skills/`
