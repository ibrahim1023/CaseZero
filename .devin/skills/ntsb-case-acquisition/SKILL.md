---
name: ntsb-case-acquisition
description: Add a real NTSB case to fixtures/real-cases/ as a benchmark or demo case. Use whenever curating a new case manifest, verifying checksums, or screening a docket for redistribution constraints.
---

# Acquiring a Real NTSB Case

Real evidence only (spec §2.1). This skill produces a committed manifest, not
committed artifacts.

## Selection bar (spec §9)

- Official final finding exists and is public.
- Public docket accessible; meaningful pre-conclusion evidence exists.
- Cause is non-trivial; finding can be hidden during investigation.
- Prefer a case whose modality mix the benchmark set does not already cover.

## Procedure

1. Resolve metadata via CAROL FileExport or the developer API (`GetCase`).
   Record NTSB number, event date, report publication date.
2. Build `manifest.json` in `fixtures/real-cases/<case-id>/`: every docket
   document with title, document_type, source_url, published_at, and
   visibility per the deterministic rules in ADR 0007 (manual override only
   with a `"curation": "manual"` note explaining why).
3. Set the case cutoff before the adopted report's publication date.
4. Write `retrieval.sh`: downloads each source_url, verifies SHA-256 against
   the manifest, exits non-zero on any mismatch. Rate-limit ≥5 s between
   requests.
5. Screen for third-party copyright notices (manufacturer manuals, submitted
   exhibits). Flag them in the manifest; they are fetched on demand, never
   redistributed.
6. Hand-transcribe the official probable cause + contributing factors into
   `official-finding.json` with the report's URL. This file is evaluation
   material — it must be unreachable to blind stages.
7. Run the retrieval script once, confirm all checksums verify, then delete
   downloaded artifacts (gitignored `data/` anyway).

## Done means

Manifest validates against `DocketManifest`, retrieval script verifies, and
the case appears in `benchmark/cases.json` only if it will actually be run.
Attribute: "Courtesy: National Transportation Safety Board".
