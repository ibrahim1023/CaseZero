# Verification Loops

Run the narrowest loop that proves the change; run the wider loop before
claiming completion. No success claim without command output in front of you.

## Loop 1 — While iterating (per change)

```bash
uv run pytest <exact/test/path.py> -v        # the focused test, written first
uv run ruff check <changed files>
uv run mypy <changed package>
```

## Loop 2 — Before claiming a task complete

```bash
uv run ruff check .
uv run mypy
uv run pytest                               # full offline gate: no network, no models
```

All three must pass. If a change touched the visibility boundary, the job
queue, or locking, also run the attack fixtures:

```bash
uv run pytest tests/blindness/ -v
uv run pytest tests/resume/ -v              # if workflow state changed
```

## Loop 3 — Before a benchmark run

1. `uv run pytest` green.
2. Blindness checklist from `.devin/skills/blind-benchmark-run/SKILL.md`
   completed and recorded in the run manifest.
3. Evidence-set hash recomputed and matches the manifest.
4. Previous report for the case (if any) reviewed for regression.

## Loop 4 — Hosted/live checks (opt-in, never in ordinary CI)

```bash
CASEZERO_HOSTED_TEST=1 uv run pytest -m hosted -v
uv run python scripts/verify_hosted_supabase.py
CASEZERO_LIVE=1 uv run pytest -m live -v    # real NTSB / Context.dev / Hyperfusion checks
```

Hosted checks require an explicitly non-production Supabase project. Local
Supabase/Docker is not a runtime or required verification dependency.

## Loop 5 — Before anything becomes public

Review the README public-communication checklist against the exact artifact,
including screenshots, demo data, page metadata, social-preview cards, and
marketing copy. Confirm source links and attribution; absence of unapproved
NTSB branding or implied affiliation; visual and data-level separation of
official findings from persistently labeled AI output; cautious handling of
third-party material; no unsupported blame, negligence, fault, misconduct, or
liability claims; visible uncertainty; and a correction/removal process. Stop
for qualified legal review when any README higher-risk trigger applies.

## Loop 6 — After every phase

After the full phase gate is green and all phase tasks have clean individual
commits, invoke the repository `explain-diff-html` skill. Compare the commit
immediately before the phase with the final phase commit, while separately
identifying any unrelated working-tree changes. Generate the self-contained
HTML under `/tmp`, inspect and correct it, then report the exact path and any
verification limitations. Do not mark the phase complete until this artifact
exists. The HTML is ephemeral and must not be committed to the repository.

## Evidence rules

- Paste or summarize actual command output when reporting status; never claim
  "tests pass" from memory.
- A skipped test is not a passing test. Report skip counts.
- If the full gate was not run, say exactly which loop was run.
