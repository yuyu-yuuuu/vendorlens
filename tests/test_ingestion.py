from pathlib import Path

from sqlalchemy import select

from vendorlens.ingestion import IngestionService
from vendorlens.models import DocumentChunk, Project
from vendorlens.storage import LocalDocumentStorage


def test_ingestion_is_idempotent_and_versions_changed_content(session, tmp_path: Path):
    project = Project(name="ERP 选型")
    session.add(project)
    session.commit()
    service = IngestionService(LocalDocumentStorage(tmp_path / "files"))

    first = service.ingest(
        session,
        project_id=project.id,
        filename="需求.md",
        media_type="text/markdown",
        content="# 目标\n首期预算 80 万元。".encode(),
    )
    session.commit()
    duplicate = service.ingest(
        session,
        project_id=project.id,
        filename="需求.md",
        media_type="text/markdown",
        content="# 目标\n首期预算 80 万元。".encode(),
    )
    session.commit()
    changed = service.ingest(
        session,
        project_id=project.id,
        filename="需求.md",
        media_type="text/markdown",
        content="# 目标\n首期预算 85 万元。".encode(),
    )
    session.commit()

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.version_id == first.version_id
    assert changed.version_number == 2
    assert changed.version_id != first.version_id
    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.version_id == first.version_id)
            .order_by(DocumentChunk.ordinal)
        )
    )
    assert [chunk.chunk_type for chunk in chunks] == ["parent", "child"]
    assert chunks[1].parent_chunk_id == chunks[0].id
