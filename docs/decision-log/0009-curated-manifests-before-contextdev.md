# 0009 — Curated manifests before live Context.dev validation

**Date:** 2026-08-25 · **Status:** Accepted · **Owner decision:** user-directed

## Problem

The initial benchmark needs trustworthy evidence-set boundaries, source URLs,
visibility decisions, and third-party rights review. Using automated docket
extraction before establishing a reviewed reference would make omissions and
misclassifications difficult to detect, while deferring Context.dev entirely
would leave an approved acquisition integration unvalidated.

## Decision

Manually curate and rights-review the initial case manifests first. The curated
manifests are the reference for case completeness and classification; they are
not generated from Context.dev output.

After the reference set exists, run live Context.dev extraction against at
least one curated docket before the Phase 7 five-case go/no-go. Compare its
validated `DocketManifest` with the reviewed reference and record omissions,
extras, URL differences, and classification ambiguities. Context.dev performs
source discovery only. CaseZero must still:

- validate source URLs;
- download original NTSB artifacts itself;
- compute and preserve SHA-256 checksums and provenance;
- classify visibility deterministically; and
- make Context.dev unavailable to blind investigation workers.

Manifest change detection for newly published evidence remains later active-
case work; it is not required to curate the initial reference set.

## Consequences

Curation costs more manual effort up front, but provides a concrete oracle for
measuring extraction quality and prevents an automated crawler from silently
defining the benchmark evidence set. Context.dev is not on the critical path
for Phase 1 processor development, but its real integration cannot be called
accepted until the post-curation live comparison and blindness checks pass.
