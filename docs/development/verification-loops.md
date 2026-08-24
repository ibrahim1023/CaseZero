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
uv run mypy packages/ apps/api/
uv run pytest                               # full offline gate: no network, no models
supabase test db                            # RLS/visibility policy tests, if migrations changed
```

All four must pass. If a change touched the visibility boundary, the job
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

## Loop 4 — Live checks (opt-in, manual, never in CI)

```bash
CASEZERO_LIVE=1 uv run pytest -m live -v    # real NTSB fetch / real model smoke
```

## Evidence rules

- Paste or summarize actual command output when reporting status; never claim
  "tests pass" from memory.
- A skipped test is not a passing test. Report skip counts.
- If the full gate was not run, say exactly which loop was run.
