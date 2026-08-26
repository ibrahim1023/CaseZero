import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from casezero_evidence import ProcessingDisposition
from casezero_ntsb.curation import load_curated_manifest, validate_curated_manifest

MANIFEST_PATH = Path(__file__).with_name("manifest.json")
OUTPUT_ROOT = Path("data/real-cases/cen22fa375")


def main() -> None:
    if os.getenv("CASEZERO_LIVE") != "1":
        raise RuntimeError("CASEZERO_LIVE=1 is required")
    manifest = load_curated_manifest(MANIFEST_PATH)
    report = validate_curated_manifest(manifest)
    if not report.valid:
        raise RuntimeError("; ".join(report.errors))

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    processable = {ProcessingDisposition.AI_ALLOWED}
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        downloaded = 0
        for index, item in enumerate(manifest.items, start=1):
            if item.processing_disposition not in processable:
                print(f"{index:02d} SKIP {item.processing_disposition.value} {item.title}")
                continue
            if downloaded:
                time.sleep(5)
            response = client.get(str(item.source_url))
            response.raise_for_status()
            host = urlparse(str(response.url)).hostname
            if host != "ntsb.gov" and (host is None or not host.endswith(".ntsb.gov")):
                raise RuntimeError(f"item {index} redirected outside ntsb.gov")
            checksum = hashlib.sha256(response.content).hexdigest()
            if checksum != item.expected_checksum:
                raise RuntimeError(f"item {index} checksum mismatch")
            suffix = item.file_type or "bin"
            destination = OUTPUT_ROOT / f"{index:02d}.{suffix}"
            destination.write_bytes(response.content)
            print(f"{index:02d} OK {checksum} {item.title}")
            downloaded += 1


if __name__ == "__main__":
    main()
