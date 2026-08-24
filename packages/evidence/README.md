# packages/evidence

Typed investigation state: `SourceDocument`, `EvidenceItem`, `SourceLocator`,
`Claim`, `InvestigationEntity`, `TimelineEvent`, provenance helpers, and the
`SourceStore` interface (content-addressed, immutable, SHA-256).

No model calls, no stage logic. Contracts are fixed by
`docs/superpowers/specs/2026-08-24-casezero-foundation-design.md`; changing
them is a breaking change requiring test + ADR + doc updates in the same commit.

Status: planned (Phase 0).
