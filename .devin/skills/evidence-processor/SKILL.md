---
name: evidence-processor
description: Add a new evidence-modality processor (or extend an existing one) in packages/ingestion without breaking provenance or the deterministic-first rule. Use whenever adding support for a file type or evidence kind.
---

# Adding an Evidence Processor

The contract: `Processor.process(source: StoredSource) -> list[EvidenceItem]`.
A processor is accepted when every item it emits can take a reviewer back to
the exact bytes it came from.

## Checklist

1. **Deterministic first.** Prove conventional code cannot extract the signal
   before reaching for a model (spec §2.5). Structure, timestamps, checksums,
   and locators are always deterministic; only semantic interpretation may
   use AI.
2. **SourceLocator or it didn't happen.** Every `EvidenceItem` carries a
   locator that resolves: page/paragraph (PDF), row/columns (table),
   region (image), segment ms (audio), char range (text). Write the test that
   resolves the locator against the fixture artifact and compares content.
3. **Fixture-driven test.** One directory per scenario under the processor's
   `tests/fixtures/`: synthetic input artifact + `expected.evidence.json`.
   Synthetic fixtures are allowed only in tests (spec §2.1). No live model or
   network in default runs.
4. **Extraction honesty.** AI-derived items set `extraction_method=AI` and a
   confidence; low-confidence outputs are flagged, never silently dropped.
   Derived artifacts (OCR text, transcripts) record extraction model +
   version and never replace the immutable original.
5. **Failure behavior.** Corrupt/unsupported input raises a typed error the
   ingestion pipeline records on the document row; it never crashes the case.

## Done means

Focused tests green, then Loop 1 of
`docs/development/verification-loops.md`. If the processor introduces a new
dependency, the PR body justifies it against ADR 0006's table.
