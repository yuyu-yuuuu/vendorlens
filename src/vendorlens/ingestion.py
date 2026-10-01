from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vendorlens.models import Document, DocumentChunk, DocumentVersion
from vendorlens.parsers import parse_document
from vendorlens.storage import DocumentStorage


@dataclass(frozen=True)
class IngestionResult:
    document_id: str
    version_id: str
    version_number: int
    created: bool
    chunk_count: int


class IngestionService:
    def __init__(self, storage: DocumentStorage):
        self.storage = storage

    def ingest(
        self,
        session: Session,
        *,
        project_id: str,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> IngestionResult:
        stored = self.storage.put(project_id, filename, content)
        with session.begin_nested():
            document = session.scalar(
                select(Document).where(
                    Document.project_id == project_id, Document.logical_name == filename
                )
            )
            if document is None:
                document = Document(
                    project_id=project_id, logical_name=filename, media_type=media_type
                )
                session.add(document)
                session.flush()

            existing = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.file_hash == stored.sha256,
                )
            )
            if existing is not None:
                return IngestionResult(
                    document.id,
                    existing.id,
                    existing.version_number,
                    False,
                    len(existing.chunks),
                )

            next_version = (
                session.scalar(
                    select(func.max(DocumentVersion.version_number)).where(
                        DocumentVersion.document_id == document.id
                    )
                )
                or 0
            ) + 1
            parsed = parse_document(filename, content)
            version = DocumentVersion(
                document_id=document.id,
                version_number=next_version,
                file_hash=stored.sha256,
                storage_key=stored.key,
                status="parsed",
            )
            session.add(version)
            session.flush()
            for ordinal, chunk in enumerate(parsed):
                session.add(
                    DocumentChunk(
                        version_id=version.id,
                        ordinal=ordinal,
                        text=chunk.text,
                        text_hash=chunk.text_hash,
                        location=chunk.location.as_dict(),
                        chunk_metadata=chunk.metadata,
                    )
                )
            session.flush()
            return IngestionResult(
                document.id, version.id, next_version, True, len(parsed)
            )

