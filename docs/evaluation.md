# Evaluation

Evaluation is a first-class subsystem, not a report written at the end. The
benchmark harness (`benchmark/`) compares locked blind assessments against
official NTSB findings. Numbers are always measured; never invent them.

## Metric set (from spec §35)

| Metric | Method | Kind |
|---|---|---|
| Cause agreement | Structured factor extraction from both assessments + judge comparison + manual review on benchmark cases | Judge + human |
| Contributing-factor recall | `official factors recovered / official factors`, factors matched by judge with rubric | Judge |
| Citation precision | Sampled claims: does the cited evidence actually support the claim? | Judge + human sample |
| Unsupported-claim rate | `unsupported substantive claims / total substantive claims` — target trends to 0 | Judge + human sample |
| Contradiction discovery | Did the system surface evidence weakening its own leading hypothesis? (graded from persisted `hypothesis_tests` + critiques, not vibes) | Deterministic + judge |
| Confidence calibration | Mean confidence vs accuracy; reliability diagram only when N ≥ 30; formal ECE deferred until N ≥ 100 | Deterministic |
| Abstention quality | Coverage–accuracy tradeoff; was `INSUFFICIENT_EVIDENCE` returned exactly when evidence was inadequate? | Deterministic + human |
| Temporal leakage | **Hard requirement: 0 incidents.** Deterministic audit over spans and access logs | Deterministic gate |
| Cost & runtime | Per-case averages from spans | Deterministic |

## Judges

- Judge model differs from the generator (rubric-based, JSON verdict +
  reasoning, one claim per call).
- A judge is calibrated against ≥20 human-labeled examples before its scores
  gate anything; agreement (Cohen's κ) recorded in the benchmark report.
- Position and verbosity bias controls: rubric states criteria explicitly;
  pairwise order randomized where used.
- Judge cost is tracked as a separate line in the benchmark report.

## Harness

```text
benchmark/
  cases.json      benchmark case registry (id, NTSB number, manifest, official finding ref)
  runner/         casezero benchmark run — acquire → blind investigate → lock → reveal → grade
  graders/        deterministic metrics + judge wrappers
  results/        raw run outputs (gitignored)
  reports/        published measured reports (committed)
```

Each report records: system version, evidence-set hash, prompt versions,
model per stage, judge model + calibration κ, and the metric table. A report
without these hashes is invalid.

## Leakage audit (gate)

Deterministic scan over the run's spans and access logs:

1. No blind-stage span references a document whose visibility ≠
   `INVESTIGATION_EVIDENCE`.
2. No blind-stage tool call hit the network (egress is disabled for blind
   workers; attempts are logged).
3. No evidence item's source was published after the case cutoff.
4. Lock occurred before any evaluation-role read of official material.

Any violation fails the run regardless of other scores.

## Datasets

- **Golden benchmark set:** the 3–5 first-milestone cases, human-reviewed,
  in `benchmark/cases.json` with pinned manifest versions.
- **Regression set:** rows added every time an eval or leakage failure is
  fixed; runs in CI offline.
- Small-N honesty: with 5–50 cases, report exact binomial intervals and avoid
  CLT-based significance claims.
