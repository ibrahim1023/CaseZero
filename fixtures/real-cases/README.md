# fixtures/real-cases/

Case manifests for real NTSB investigations. One directory per case:

```text
real-cases/<case-id>/
  manifest.json           DocketManifest + per-document visibility + retrieval metadata
  official-finding.json   probable cause + contributing factors, hand-transcribed from the adopted report
  retrieval.sh            re-fetches originals from source URLs and verifies SHA-256
```

**Never commit source artifacts.** Use only officially public material. Dockets
can contain third-party copyrighted submissions, so public availability must
not be treated as permission to republish or train on them; commit manifests,
URLs, and checksums only (spec §9), and link to the official docket when rights
are unclear. Acquisition uses plain-text attribution: “Source: National
Transportation Safety Board.” Never use the NTSB seal, logo, or protected
branding without written permission.

Curated manifests are an accepted MVP path (spec §3.4); mark curated entries
with `"curation": "manual"` in the manifest.
