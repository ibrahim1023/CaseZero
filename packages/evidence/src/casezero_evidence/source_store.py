import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID


class StoreIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredSource:
    case_id: UUID
    original_filename: str
    storage_path: Path
    checksum: str
    size_bytes: int


class SourceStore(Protocol):
    def put(self, case_id: UUID, filename: str, data: bytes) -> StoredSource: ...

    def get(self, storage_path: Path) -> bytes: ...


class LocalSourceStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, case_id: UUID, filename: str, data: bytes) -> StoredSource:
        checksum = hashlib.sha256(data).hexdigest()
        storage_path = Path(checksum[:2]) / checksum
        destination = self._root / storage_path
        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            with destination.open("xb") as artifact:
                artifact.write(data)
        except FileExistsError:
            if destination.read_bytes() != data:
                raise StoreIntegrityError(
                    f"stored artifact {storage_path} does not match its content address"
                ) from None

        return StoredSource(
            case_id=case_id,
            original_filename=filename,
            storage_path=storage_path,
            checksum=checksum,
            size_bytes=len(data),
        )

    def get(self, storage_path: Path) -> bytes:
        if not self._is_content_addressed_path(storage_path):
            raise ValueError("storage_path must be a content-addressed path")
        return (self._root / storage_path).read_bytes()

    @staticmethod
    def _is_content_addressed_path(storage_path: Path) -> bool:
        parts = storage_path.parts
        if len(parts) != 2:
            return False
        prefix, checksum = parts
        return (
            len(prefix) == 2
            and len(checksum) == 64
            and prefix == checksum[:2]
            and all(character in "0123456789abcdef" for character in checksum)
        )
