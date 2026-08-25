import hashlib
from pathlib import Path

import pytest
from casezero_evidence.artifact_store import ArtifactIntegrityError, LocalArtifactStore
from casezero_evidence.processing import DerivedArtifactKind


def test_artifact_store_round_trips_by_kind_and_checksum(tmp_path: Path) -> None:
    data = b'{"blocks":[]}'
    store = LocalArtifactStore(tmp_path)

    stored = store.put(DerivedArtifactKind.DOCUMENT_STRUCTURE, data)

    assert stored.checksum == hashlib.sha256(data).hexdigest()
    assert stored.storage_path.parts[0] == "document-structure"
    assert store.get(stored.storage_path) == data


def test_artifact_store_is_idempotent_and_detects_tampering(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    stored = store.put(DerivedArtifactKind.TABLE_PROFILE, b"profile")
    assert store.put(DerivedArtifactKind.TABLE_PROFILE, b"profile") == stored
    (tmp_path / stored.storage_path).write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError, match="content address"):
        store.put(DerivedArtifactKind.TABLE_PROFILE, b"profile")
