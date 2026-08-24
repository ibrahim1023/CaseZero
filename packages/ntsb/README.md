# packages/ntsb

Acquisition layer. NTSB case metadata (CAROL FileExport, developer API),
docket discovery (Context.dev → validated `DocketManifest`), source
downloader, SHA-256 provenance, deterministic visibility classification.

Deterministic only — no model calls. Rate-limited (≥5 s between NTSB
requests). See ADR 0006 and `docs/decision-log/0007-temporal-blindness-enforcement.md`.

Status: planned (Phase 0 plan: `docs/superpowers/plans/2026-08-24-phase0-case-acquisition.md`).
