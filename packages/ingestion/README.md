# packages/ingestion

Modality processors: `process(source) -> list[EvidenceItem]`. PDF (Docling,
scan-routed OCR), CSV/XLSX (Polars, deterministic), images (VLM structured
observation), audio (ElevenLabs Scribe when present), TXT/HTML.

Deterministic-first; AI only for semantic interpretation. Every output item
carries a resolvable SourceLocator. See ADR 0006.

Status: planned (Phase 1).
