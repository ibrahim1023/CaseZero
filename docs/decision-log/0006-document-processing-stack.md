# 0006 — Document and evidence processing stack

**Date:** 2026-08-24 · **Status:** Accepted

## Problem

Dockets are heterogeneous: digital and scanned PDFs, CSV/XLSX flight data and
maintenance logs, TXT/HTML, photographs/diagrams, occasionally audio. Every
extracted item needs a SourceLocator back to the original, and OCR/LLMs must
be used only where deterministic extraction genuinely cannot work.

## Decision

| Modality | Tool | Notes |
|---|---|---|
| Digital PDF | **Docling** (MIT, IBM) | Structure-aware: pages, sections, tables, reading order → SourceLocators |
| Scanned PDF | PreOCR-style detection → vision OCR via Hyperfusion VLM | Route per page; never OCR a digital page |
| CSV/XLSX | **Polars** + csv-detective-style schema inference | Deterministic: typed columns, UTC normalization, derived event windows; AI interprets windows, never millions of cells |
| Images | VLM structured observation (typed schema, conservative language, `INFERRED` status, confidence) | Never presented as forensic fact |
| Audio (when present) | **ElevenLabs Scribe** | Timestamped, diarized segments → `TranscriptEvidence` linked to source audio; system fully works without it |

PyMuPDF is excluded (AGPL). Tesseract is excluded (poor accuracy on real
docket scans). Commercial per-page parsing (LlamaParse et al.) is unnecessary
at MVP volume.

## Alternatives considered

- **Unstructured hi_res / Marker** — comparable parsers; Docling wins on
  license, table quality, and maintenance trajectory.
- **pandas** — Polars is faster and stricter; aviation time series benefit
  from native time-based windowing.
- **Whisper self-hosted for audio** — viable fallback; Scribe's built-in
  diarization + word timestamps fit the evidence model directly and the spec
  pre-approves ElevenLabs.

## Consequences

Each processor is a narrow interface: `process(source) -> list[EvidenceItem]`
with deterministic pre-validation. Adding a modality = one processor + tests +
fixtures (see `.devin/skills/evidence-processor/`). Derived artifacts
(transcripts, OCR text) record their extraction model and never replace the
immutable original.
