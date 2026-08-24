# scripts/

One-off acquisition and maintenance scripts. Rules:

- Scripts are operational tools, not product code — no domain logic that
  belongs in a package.
- Anything that touches NTSB rate limits or live services documents its
  usage at the top of the file and requires `CASEZERO_LIVE=1`.
- Spikes are labeled throwaway in their filename or header and their results
  are recorded in `docs/decision-log/` (e.g. `0003a-spike-results.md`).
