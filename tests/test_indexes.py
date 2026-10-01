from pathlib import Path

import chromadb

from vendorlens.indexes import (
    ChromaVectorIndex,
    DatabaseParentRetriever,
    DocumentIndexingService,
    PersistentBM25Index,
    PersistentHybridRetriever,
    rrf_merge,
)
from vendorlens.ingestion import IngestionService
from vendorlens.models import Project
from vendorlens.retrieval import IndexedChunk
from vendorlens.storage import LocalDocumentStorage


def chunks():
    return [
        IndexedChunk(
            "c1",
            "甲供应商报价 88 万元",
            {"project_id": "p1", "version_id": "v1", "parent_id": "parent-1"},
        ),
        IndexedChunk(
            "c2",
            "乙供应商提供三年运维",
            {"project_id": "p1", "version_id": "v1", "parent_id": "parent-2"},
        ),
    ]


def test_bm25_index_survives_recreation(tmp_path: Path):
    path = tmp_path / "bm25.json"
    index = PersistentBM25Index(path)
    index.replace_version("v1", chunks())
    original_ids = [
        hit.chunk_id for hit in index.search("甲供应商报价", top_k=2, filters={"version_id": "v1"})
    ]

    restored = PersistentBM25Index(path)
    restored_ids = [
        hit.chunk_id
        for hit in restored.search("甲供应商报价", top_k=2, filters={"version_id": "v1"})
    ]

    assert restored_ids == original_ids


def test_chroma_index_replaces_version_and_filters_metadata():
    index = ChromaVectorIndex(chromadb.EphemeralClient(), collection_name="test_chunks")
    index.replace_version("v1", chunks())

    hits = index.search("甲供应商报价", top_k=2, filters={"version_id": "v1"})

    assert {hit.chunk_id for hit in hits} == {"c1", "c2"}
    assert all(hit.metadata["version_id"] == "v1" for hit in hits)


def test_rrf_merges_without_mixing_score_scales(tmp_path: Path):
    bm25 = PersistentBM25Index(tmp_path / "bm25.json")
    bm25.replace_version("v1", chunks())
    vector = ChromaVectorIndex(chromadb.EphemeralClient(), collection_name="rrf_chunks")
    vector.replace_version("v1", chunks())

    hits = rrf_merge(
        [
            bm25.search("报价", top_k=2),
            vector.search("报价", top_k=2),
        ],
        top_k=2,
    )

    assert len(hits) == 2
    assert all(hit.strategy == "hybrid" for hit in hits)


def test_database_version_indexes_children_not_large_parent(session, tmp_path: Path):
    project = Project(name="供应商评审")
    session.add(project)
    session.flush()
    ingestion = IngestionService(LocalDocumentStorage(tmp_path / "files"))
    result = ingestion.ingest(
        session,
        project_id=project.id,
        filename="方案.md",
        media_type="text/markdown",
        content="# 报价\n总报价 88 万元，交付周期 45 天。".encode(),
    )
    bm25 = PersistentBM25Index(tmp_path / "bm25.json")
    vector = ChromaVectorIndex(chromadb.EphemeralClient(), collection_name="database_chunks")

    count = DocumentIndexingService(bm25, vector).index_version(session, result.version_id)

    assert count == 1
    assert bm25.chunks[0].metadata["chunk_type"] == "child"
    assert bm25.chunks[0].metadata["parent_id"] != bm25.chunks[0].id


def test_persistent_hybrid_retrieval_expands_child_to_database_parent(session, tmp_path: Path):
    project = Project(name="供应商评审")
    session.add(project)
    session.flush()
    result = IngestionService(LocalDocumentStorage(tmp_path / "files")).ingest(
        session,
        project_id=project.id,
        filename="方案.md",
        media_type="text/markdown",
        content="# 商务报价\n甲供应商含税总报价 88 万元，交付周期 45 天。".encode(),
    )
    bm25 = PersistentBM25Index(tmp_path / "bm25.json")
    vector = ChromaVectorIndex(chromadb.EphemeralClient(), collection_name="parent_chunks")
    DocumentIndexingService(bm25, vector).index_version(session, result.version_id)
    retriever = DatabaseParentRetriever(PersistentHybridRetriever(bm25, vector))

    hits = retriever.search(
        session,
        "甲供应商报价",
        filters={"project_id": project.id, "version_id": result.version_id},
    )

    assert len(hits) == 1
    assert hits[0].matched_child.metadata["chunk_type"] == "child"
    assert "含税总报价 88 万元" in hits[0].parent.text
    assert hits[0].parent.metadata["text_hash"]
    assert hits[0].parent.metadata["version_id"] == result.version_id
