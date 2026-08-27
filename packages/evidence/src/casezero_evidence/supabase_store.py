import hashlib
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import httpx

from casezero_evidence.artifact_store import ArtifactIntegrityError, StoredArtifact
from casezero_evidence.processing import DerivedArtifactKind
from casezero_evidence.source_store import StoredSource, StoreIntegrityError


class _SupabaseStorage:
    def __init__(
        self,
        url: str,
        service_key: str,
        bucket: str,
        *,
        client: httpx.Client,
    ) -> None:
        self._url = url.rstrip("/")
        self._service_key = service_key
        self._bucket = bucket
        self._client = client

    def _object_url(self, storage_path: Path) -> str:
        encoded = "/".join(quote(part, safe="") for part in storage_path.parts)
        return f"{self._url}/storage/v1/object/{quote(self._bucket, safe='')}/{encoded}"

    def put_verified(self, storage_path: Path, data: bytes, error_type: type[RuntimeError]) -> None:
        url = self._object_url(storage_path)
        headers = {
            "apikey": self._service_key,
            "Content-Type": "application/octet-stream",
            "x-upsert": "false",
        }
        response = self._client.post(url, headers=headers, content=data, timeout=120)
        if response.status_code not in {400, 409}:
            response.raise_for_status()
        fetched = self._client.get(
            url,
            headers={"apikey": self._service_key},
            timeout=120,
        )
        fetched.raise_for_status()
        if fetched.content != data:
            raise error_type(f"hosted object {storage_path} does not match its content address")

    def get(self, storage_path: Path) -> bytes:
        if any(part in {"", ".", ".."} for part in storage_path.parts):
            raise ValueError("invalid hosted content-addressed path")
        response = self._client.get(
            self._object_url(storage_path),
            headers={"apikey": self._service_key},
            timeout=120,
        )
        response.raise_for_status()
        return response.content


class SupabaseSourceStore:
    def __init__(self, url: str, service_key: str, bucket: str, *, client: httpx.Client) -> None:
        self._storage = _SupabaseStorage(url, service_key, bucket, client=client)

    def put(self, case_id: UUID, filename: str, data: bytes) -> StoredSource:
        checksum = hashlib.sha256(data).hexdigest()
        storage_path = Path("sources") / checksum[:2] / checksum
        self._storage.put_verified(storage_path, data, StoreIntegrityError)
        return StoredSource(case_id, filename, storage_path, checksum, len(data))

    def get(self, storage_path: Path) -> bytes:
        return self._storage.get(storage_path)


class SupabaseArtifactStore:
    def __init__(self, url: str, service_key: str, bucket: str, *, client: httpx.Client) -> None:
        self._storage = _SupabaseStorage(url, service_key, bucket, client=client)

    def put(self, kind: DerivedArtifactKind, data: bytes) -> StoredArtifact:
        checksum = hashlib.sha256(data).hexdigest()
        kind_path = kind.value.lower().replace("_", "-")
        storage_path = Path("derived") / kind_path / checksum[:2] / checksum
        self._storage.put_verified(storage_path, data, ArtifactIntegrityError)
        return StoredArtifact(kind, storage_path, checksum, len(data))

    def get(self, storage_path: Path) -> bytes:
        return self._storage.get(storage_path)
