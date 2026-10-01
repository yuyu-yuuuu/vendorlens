from contextlib import nullcontext
from pathlib import Path

import chromadb

from vendorlens.coordinator import DatabaseRetrievalCoordinator
from vendorlens.indexes import (
    ChromaVectorIndex,
    DatabaseParentRetriever,
    DocumentIndexingService,
    PersistentBM25Index,
    PersistentHybridRetriever,
)
from vendorlens.ingestion import IngestionService
from vendorlens.models import Project
from vendorlens.schemas import PlanDimension, ResearchPlan, ResearchSubtask
from vendorlens.storage import LocalDocumentStorage


def test_coordinator_searches_only_latest_indexed_document_version(session, tmp_path: Path):
    project = Project(name="供应商评审")
    session.add(project)
    session.flush()
    ingestion = IngestionService(LocalDocumentStorage(tmp_path / "files"))
    old = ingestion.ingest(
        session,
        project_id=project.id,
        filename="报价.md",
        media_type="text/markdown",
        content="# 报价\n旧版总报价 99 万元。".encode(),
    )
    new = ingestion.ingest(
        session,
        project_id=project.id,
        filename="报价.md",
        media_type="text/markdown",
        content="# 报价\n新版总报价 88 万元。".encode(),
    )
    bm25 = PersistentBM25Index(tmp_path / "bm25.json")
    vector = ChromaVectorIndex(chromadb.EphemeralClient(), collection_name="coordinator")
    indexer = DocumentIndexingService(bm25, vector)
    indexer.index_version(session, old.version_id)
    indexer.index_version(session, new.version_id)
    coordinator = DatabaseRetrievalCoordinator(
        lambda: nullcontext(session),
        DatabaseParentRetriever(PersistentHybridRetriever(bm25, vector)),
    )
    plan = ResearchPlan(
        objective="比较报价",
        dimensions=[PlanDimension(name="价格", weight=1.0, description="总价")],
        subtasks=[
            ResearchSubtask(
                id="price",
                title="报价",
                queries=["总报价"],
                dimension_names=["价格"],
            )
        ],
    )

    evidence = coordinator.retrieve(plan, project.id)["price"]

    assert evidence
    assert all(item.document_version_id == new.version_id for item in evidence)
    assert "88 万元" in evidence[0].quote
    assert "99 万元" not in evidence[0].quote
