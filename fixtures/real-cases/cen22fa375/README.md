# CEN22FA375 reference docket

Source: National Transportation Safety Board. Official docket:
https://data.ntsb.gov/Docket/?NTSBNumber=CEN22FA375

The manifest inventories all 15 public docket entries. It does not imply that
public availability grants reuse or external-model-processing rights.

Three NTSB-authored items are approved for hosted Phase 1 retrieval:

- NTSB Examination Report — `AI_ALLOWED`
- Fire Specialist's Factual Report — `AI_ALLOWED`
- Cockpit Display Specialist's Factual Report CSV attachment — `AI_ALLOWED`

The Medical Factual Report remains `LOCAL_ONLY` and is audited as skipped; with
no approved local model/storage route, its bytes are not downloaded or processed.

Items with unclear authorship, third-party records, manufacturer excerpts,
personal statements, externally sourced data, or explicit third-party photo
credit remain `LINK_ONLY`. Their bytes are not downloaded by the retrieval
script, committed, or sent to models.

Run:

```bash
CASEZERO_LIVE=1 uv run python fixtures/real-cases/cen22fa375/retrieval.py
```

Artifacts are written under gitignored `data/real-cases/cen22fa375/` and are
verified against the SHA-256 values pinned in `manifest.json`.
