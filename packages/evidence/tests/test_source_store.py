import hashlib
from pathlib import Path
from uuid import UUID

import pytest
from casezero_evidence.source_store import LocalSourceStore, StoreIntegrityError

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")


def test_local_store_round_trips_content_by_sha256(tmp_path: Path) -> None:
    data = b"synthetic docket artifact"
    store = LocalSourceStore(tmp_path)

    stored = store.put(CASE_ID, "factual-report.pdf", data)

    assert stored.checksum == hashlib.sha256(data).hexdigest()
    assert stored.storage_path == Path(stored.checksum[:2]) / stored.checksum
    assert stored.original_filename == "factual-report.pdf"
    assert stored.size_bytes == len(data)
    assert store.get(stored.storage_path) == data


def test_repeated_put_of_identical_content_is_idempotent(tmp_path: Path) -> None:
    store = LocalSourceStore(tmp_path)

    first = store.put(CASE_ID, "first-name.pdf", b"same bytes")
    second = store.put(CASE_ID, "second-name.pdf", b"same bytes")

    assert first.storage_path == second.storage_path
    assert len(tuple(path for path in tmp_path.rglob("*") if path.is_file())) == 1


def test_store_refuses_tampered_existing_content(tmp_path: Path) -> None:
    store = LocalSourceStore(tmp_path)
    stored = store.put(CASE_ID, "report.pdf", b"authoritative bytes")
    (tmp_path / stored.storage_path).write_bytes(b"tampered bytes")

    with pytest.raises(StoreIntegrityError, match="does not match its content address"):
        store.put(CASE_ID, "report.pdf", b"authoritative bytes")


def test_get_rejects_paths_outside_store_root(tmp_path: Path) -> None:
    store = LocalSourceStore(tmp_path)

    with pytest.raises(ValueError, match="content-addressed path"):
        store.get(Path("../outside"))
