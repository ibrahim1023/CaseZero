import hashlib
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx
from casezero_evidence import DerivedArtifactKind
from casezero_evidence.source_store import StoreIntegrityError
from casezero_evidence.supabase_store import SupabaseArtifactStore, SupabaseSourceStore

URL = "https://project.supabase.co"
KEY = "service-role-test"
BUCKET = "casezero-sources"
CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DATA = b"official NTSB source"
SHA = hashlib.sha256(DATA).hexdigest()
OBJECT_URL = f"{URL}/storage/v1/object/{BUCKET}/sources/{SHA[:2]}/{SHA}"


@respx.mock
def test_source_upload_is_private_content_addressed_and_verified() -> None:
    upload = respx.post(OBJECT_URL).mock(return_value=httpx.Response(200, json={"Key": "stored"}))
    respx.get(OBJECT_URL).mock(return_value=httpx.Response(200, content=DATA))
    with httpx.Client() as client:
        stored = SupabaseSourceStore(URL, KEY, BUCKET, client=client).put(
            CASE_ID, "report.pdf", DATA
        )
    assert "authorization" not in upload.calls[0].request.headers
    assert upload.calls[0].request.headers["apikey"] == KEY
    assert upload.calls[0].request.headers["x-upsert"] == "false"
    assert stored.checksum == SHA
    assert stored.storage_path == Path("sources") / SHA[:2] / SHA


@respx.mock
def test_existing_matching_object_is_idempotent() -> None:
    respx.post(OBJECT_URL).mock(return_value=httpx.Response(409))
    respx.get(OBJECT_URL).mock(return_value=httpx.Response(200, content=DATA))
    with httpx.Client() as client:
        stored = SupabaseSourceStore(URL, KEY, BUCKET, client=client).put(
            CASE_ID, "report.pdf", DATA
        )
    assert stored.checksum == SHA


@respx.mock
def test_existing_mismatched_object_fails_closed() -> None:
    respx.post(OBJECT_URL).mock(return_value=httpx.Response(409))
    respx.get(OBJECT_URL).mock(return_value=httpx.Response(200, content=b"different"))
    with httpx.Client() as client, pytest.raises(StoreIntegrityError, match="does not match"):
        SupabaseSourceStore(URL, KEY, BUCKET, client=client).put(
            CASE_ID, "report.pdf", DATA
        )


@respx.mock
def test_artifact_key_includes_kind() -> None:
    path = f"derived/document-structure/{SHA[:2]}/{SHA}"
    url = f"{URL}/storage/v1/object/casezero-derived/{path}"
    respx.post(url).mock(return_value=httpx.Response(200))
    respx.get(url).mock(return_value=httpx.Response(200, content=DATA))
    with httpx.Client() as client:
        stored = SupabaseArtifactStore(URL, KEY, "casezero-derived", client=client).put(
            DerivedArtifactKind.DOCUMENT_STRUCTURE, DATA
        )
    assert stored.storage_path == Path(path)
