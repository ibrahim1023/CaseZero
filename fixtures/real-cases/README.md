# fixtures/real-cases/

Case manifests for real NTSB investigations. One directory per case:

```text
real-cases/<case-id>/
  manifest.json           DocketManifest + per-document visibility + retrieval metadata
  official-finding.json   probable cause + contributing factors, hand-transcribed from the adopted report
  retrieval.sh            re-fetches originals from source URLs and verifies SHA-256
```

**Never commit source artifacts.** NTSB-authored material is public domain,
but dockets can contain third-party copyrighted submissions; commit manifests,
URLs, and checksums only (spec §9). Acquisition attributes "Courtesy: National
Transportation Safety Board".

Curated manifests are an accepted MVP path (spec §3.4); mark curated entries
with `"curation": "manual"` in the manifest.
