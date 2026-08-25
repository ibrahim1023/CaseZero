import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from casezero_evidence.processing import DerivedArtifactKind


class ArtifactIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    kind: DerivedArtifactKind
    storage_path: Path
    checksum: str
    byte_size: int


class ArtifactStore(Protocol):
    def put(self, kind: DerivedArtifactKind, data: bytes) -> StoredArtifact: ...

    def get(self, storage_path: Path) -> bytes: ...


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, kind: DerivedArtifactKind, data: bytes) -> StoredArtifact:
        checksum = hashlib.sha256(data).hexdigest()
        kind_path = kind.value.lower().replace("_", "-")
        storage_path = Path(kind_path) / checksum[:2] / checksum
        destination = self._root / storage_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as artifact:
                artifact.write(data)
        except FileExistsError:
            if destination.read_bytes() != data:
                raise ArtifactIntegrityError(
                    f"stored artifact {storage_path} does not match its content address"
                ) from None
        return StoredArtifact(
            kind=kind,
            storage_path=storage_path,
            checksum=checksum,
            byte_size=len(data),
        )

    def get(self, storage_path: Path) -> bytes:
        resolved = (self._root / storage_path).resolve()
        if not resolved.is_relative_to(self._root) or len(storage_path.parts) != 3:
            raise ValueError("storage_path must be a derived content-addressed path")
        return resolved.read_bytes()
