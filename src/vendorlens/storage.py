from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int


class DocumentStorage(Protocol):
    def put(self, project_id: str, filename: str, content: bytes) -> StoredObject: ...

    def read(self, key: str) -> bytes: ...


class LocalDocumentStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, project_id: str, filename: str, content: bytes) -> StoredObject:
        digest = hashlib.sha256(content).hexdigest()
        safe_name = Path(filename).name
        key = f"{project_id}/{digest}/{safe_name}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return StoredObject(key=key, sha256=digest, size=len(content))

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("storage key escapes configured root")
        return path
