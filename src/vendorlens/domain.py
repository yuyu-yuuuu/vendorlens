from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    INDEXED = "indexed"
    OCR_REQUIRED = "ocr_required"
    FAILED = "failed"


@dataclass(frozen=True)
class SourceLocation:
    page: int | None = None
    paragraph: int | None = None
    heading_path: tuple[str, ...] = ()
    sheet: str | None = None
    cell_range: str | None = None
    line_start: int | None = None
    line_end: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "paragraph": self.paragraph,
            "heading_path": list(self.heading_path),
            "sheet": self.sheet,
            "cell_range": self.cell_range,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass(frozen=True)
class ParsedChunk:
    text: str
    location: SourceLocation
    text_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    score: float
    rank: int
    strategy: str

