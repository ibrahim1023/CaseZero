# benchmark/

The benchmark harness. `casezero benchmark run` executes: acquire → blind
investigate → lock → reveal → grade, and emits a report with the metric set
from `docs/evaluation.md`.

- `cases.json` — registry of benchmark cases (committed). One entry per case:
  NTSB number, manifest pointer, cutoff, official-finding reference.
- `runner/` — orchestrates one case end to end.
- `graders/` — deterministic metrics + calibrated judge wrappers.
- `results/` — raw run outputs (gitignored).
- `reports/` — measured reports (committed). A report is invalid without
  system version, evidence-set hash, prompt versions, and judge calibration.

Numbers are measured, never invented. Temporal leakage is a hard gate: any
incident fails the run regardless of other scores.
