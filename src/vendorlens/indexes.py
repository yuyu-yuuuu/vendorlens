from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import chromadb
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session

from vendorlens.domain import ExpandedSearchHit, SearchHit
from vendorlens.models import Document, DocumentChunk, DocumentVersion
from vendorlens.retrieval import EmbeddingProvider, HashEmbeddingProvider, IndexedChunk, tokenize


def _chroma_where(filters: dict[str, Any]) -> dict[str, Any]:
    clauses = [
        {key: {"$in": list(value)}}
        if isinstance(value, Collection) and not isinstance(value, (str, bytes, dict))
        else {key: value}
        for key, value in filters.items()
    ]
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _matches_filters(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        actual = metadata.get(key)
        if isinstance(expected, Collection) and not isinstance(expected, (str, bytes, dict)):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


class PersistentBM25Index:
    def __init__(self, path: Path):
        self.path = path
        self.chunks = self._load()

    def _load(self) -> list[IndexedChunk]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [IndexedChunk(**item) for item in data]

    def replace_version(self, version_id: str, chunks: list[IndexedChunk]) -> None:
        self.chunks = [
            item for item in self.chunks if item.metadata.get("version_id") != version_id
        ] + chunks
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps([asdict(item) for item in self.chunks], ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def search(
        self, query: str, *, top_k: int, filters: dict[str, Any] | None = None
    ) -> list[SearchHit]:
        eligible = [item for item in self.chunks if _matches_filters(item.metadata, filters)]
        if not eligible:
            return []
        bm25 = BM25Okapi([tokenize(item.text) for item in eligible])
        scores = bm25.get_scores(tokenize(query))
        ranking = sorted(range(len(eligible)), key=lambda i: (-scores[i], eligible[i].id))
        return [
            SearchHit(
                chunk_id=eligible[index].id,
                text=eligible[index].text,
                metadata=eligible[index].metadata,
                score=float(scores[index]),
                rank=rank,
                strategy="bm25",
            )
            for rank, index in enumerate(ranking[:top_k], 1)
        ]


class ChromaVectorIndex:
    def __init__(
        self,
        client,
        *,
        collection_name: str = "vendorlens_chunks",
        embedder: EmbeddingProvider | None = None,
    ):
        self.collection = client.get_or_create_collection(collection_name)
        self.embedder = embedder or HashEmbeddingProvider()

    @classmethod
    def from_url(cls, url: str, **kwargs) -> ChromaVectorIndex:
        parsed = urlparse(url)
        client = chromadb.HttpClient(
            host=parsed.hostname or "localhost",
            port=parsed.port or 8000,
            ssl=parsed.scheme == "https",
        )
        return cls(client, **kwargs)

    def replace_version(self, version_id: str, chunks: list[IndexedChunk]) -> None:
        try:
            self.collection.delete(where={"version_id": version_id})
        except Exception as error:
            if "does not exist" not in str(error).lower():
                raise
        if not chunks:
            return
        self.collection.upsert(
            ids=[item.id for item in chunks],
            documents=[item.text for item in chunks],
            embeddings=self.embedder.embed([item.text for item in chunks]),
            metadatas=[item.metadata for item in chunks],
        )

    def search(
        self, query: str, *, top_k: int, filters: dict[str, Any] | None = None
    ) -> list[SearchHit]:
        arguments: dict[str, Any] = {
            "query_embeddings": self.embedder.embed([query]),
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if filters:
            arguments["where"] = _chroma_where(filters)
        result = self.collection.query(**arguments)
        ids = result["ids"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]
        return [
            SearchHit(
                chunk_id=chunk_id,
                text=text,
                metadata=metadata,
                score=1.0 / (1.0 + distance),
                rank=rank,
                strategy="vector",
            )
            for rank, (chunk_id, text, metadata, distance) in enumerate(
                zip(ids, documents, metadatas, distances, strict=True), 1
            )
        ]


def rrf_merge(rankings: list[list[SearchHit]], *, top_k: int, rrf_k: int = 60):
    scores: dict[str, float] = {}
    hits: dict[str, SearchHit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, 1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
            hits[hit.chunk_id] = hit
    ordered = sorted(scores, key=lambda item: (-scores[item], item))[:top_k]
    return [
        SearchHit(
            chunk_id=item,
            text=hits[item].text,
            metadata=hits[item].metadata,
            score=scores[item],
            rank=rank,
            strategy="hybrid",
        )
        for rank, item in enumerate(ordered, 1)
    ]


class SearchableIndex(Protocol):
    def search(
        self, query: str, *, top_k: int, filters: dict[str, Any] | None = None
    ) -> list[SearchHit]: ...


class PersistentHybridRetriever:
    def __init__(self, bm25: SearchableIndex, vector: SearchableIndex):
        self.bm25 = bm25
        self.vector = vector

    def search(
        self,
        query: str,
        *,
        strategy: str = "hybrid",
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        rrf_k: int = 60,
    ) -> list[SearchHit]:
        if strategy == "bm25":
            return self.bm25.search(query, top_k=top_k, filters=filters)
        if strategy == "vector":
            return self.vector.search(query, top_k=top_k, filters=filters)
        if strategy != "hybrid":
            raise ValueError(f"unknown retrieval strategy: {strategy}")
        candidate_count = max(top_k * 3, top_k)
        return rrf_merge(
            [
                self.bm25.search(query, top_k=candidate_count, filters=filters),
                self.vector.search(query, top_k=candidate_count, filters=filters),
            ],
            top_k=top_k,
            rrf_k=rrf_k,
        )


class DatabaseParentRetriever:
    def __init__(self, child_retriever: PersistentHybridRetriever):
        self.child_retriever = child_retriever

    def search(
        self,
        session: Session,
        query: str,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        strategy: str = "hybrid",
    ) -> list[ExpandedSearchHit]:
        child_hits = self.child_retriever.search(
            query,
            strategy=strategy,
            top_k=top_k * 3,
            filters=filters,
        )
        parent_ids = list(dict.fromkeys(str(hit.metadata["parent_id"]) for hit in child_hits))
        if not parent_ids:
            return []
        rows = session.execute(
            select(DocumentChunk, DocumentVersion, Document)
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentChunk.id.in_(parent_ids))
        ).all()
        parents = {chunk.id: (chunk, version, document) for chunk, version, document in rows}
        expanded: list[ExpandedSearchHit] = []
        seen: set[str] = set()
        for child in child_hits:
            parent_id = str(child.metadata["parent_id"])
            row = parents.get(parent_id)
            if row is None or parent_id in seen:
                continue
            parent, version, document = row
            if version.id != child.metadata.get("version_id"):
                continue
            seen.add(parent_id)
            expanded.append(
                ExpandedSearchHit(
                    parent=SearchHit(
                        chunk_id=parent.id,
                        text=parent.text,
                        metadata={
                            "project_id": document.project_id,
                            "document_id": document.id,
                            "version_id": version.id,
                            "file_hash": version.file_hash,
                            "text_hash": parent.text_hash,
                            "location": parent.location,
                            "chunk_type": parent.chunk_type,
                        },
                        score=child.score,
                        rank=len(expanded) + 1,
                        strategy="parent_document",
                    ),
                    matched_child=child,
                )
            )
            if len(expanded) == top_k:
                break
        return expanded


class WritableIndex(Protocol):
    def replace_version(self, version_id: str, chunks: list[IndexedChunk]) -> None: ...


class DocumentIndexingService:
    def __init__(self, bm25: WritableIndex, vector: WritableIndex):
        self.bm25 = bm25
        self.vector = vector

    def index_version(self, session: Session, version_id: str) -> int:
        row = session.execute(
            select(DocumentVersion, Document)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id == version_id)
        ).one_or_none()
        if row is None:
            raise ValueError("document version does not exist")
        version, document = row
        records = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.version_id == version_id)
                .order_by(DocumentChunk.ordinal)
            )
        )
        parents_with_children = {
            item.parent_chunk_id for item in records if item.parent_chunk_id is not None
        }
        indexable = [
            item
            for item in records
            if item.chunk_type == "child" or item.id not in parents_with_children
        ]
        chunks = []
        for item in indexable:
            metadata = {
                "project_id": document.project_id,
                "document_id": document.id,
                "version_id": version.id,
                "parent_id": item.parent_chunk_id or item.id,
                "chunk_type": item.chunk_type,
                "ordinal": item.ordinal,
            }
            if document.vendor_id is not None:
                metadata["vendor_id"] = document.vendor_id
            chunks.append(IndexedChunk(id=item.id, text=item.text, metadata=metadata))
        self.bm25.replace_version(version_id, chunks)
        self.vector.replace_version(version_id, chunks)
        version.status = "indexed"
        session.flush()
        return len(chunks)
