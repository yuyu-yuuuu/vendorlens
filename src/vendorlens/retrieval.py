from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

from rank_bm25 import BM25Okapi

from vendorlens.domain import ExpandedSearchHit, SearchHit


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9_.%-]+", lowered)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", lowered)
    chinese = [run[i : i + 2] for run in chinese_runs for i in range(max(1, len(run) - 1))]
    return latin + chinese


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbeddingProvider:
    """Deterministic offline embedding for tests and reproducible baselines."""

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in tokenize(text):
                raw = hashlib.blake2b(token.encode(), digest_size=8).digest()
                number = int.from_bytes(raw, "big")
                vector[number % self.dimensions] += 1.0 if number & 1 else -1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


@dataclass(frozen=True)
class IndexedChunk:
    id: str
    text: str
    metadata: dict[str, Any]


def _matches(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    return not filters or all(metadata.get(key) == value for key, value in filters.items())


class HybridRetriever:
    def __init__(self, chunks: list[IndexedChunk], embedder: EmbeddingProvider | None = None):
        self.chunks = chunks
        self.embedder = embedder or HashEmbeddingProvider()
        self.tokens = [tokenize(chunk.text) for chunk in chunks]
        self.bm25 = BM25Okapi(self.tokens)
        self.vectors = self.embedder.embed([chunk.text for chunk in chunks])

    def search(
        self,
        query: str,
        *,
        strategy: str = "hybrid",
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        rrf_k: int = 60,
    ) -> list[SearchHit]:
        eligible = [i for i, chunk in enumerate(self.chunks) if _matches(chunk.metadata, filters)]
        bm25_scores = self.bm25.get_scores(tokenize(query))
        query_vector = self.embedder.embed([query])[0]
        vector_scores = [
            sum(a * b for a, b in zip(query_vector, vector, strict=True)) for vector in self.vectors
        ]
        bm25_rank = sorted(eligible, key=lambda i: (-bm25_scores[i], self.chunks[i].id))
        vector_rank = sorted(eligible, key=lambda i: (-vector_scores[i], self.chunks[i].id))

        if strategy == "bm25":
            ranked = [(i, float(bm25_scores[i])) for i in bm25_rank]
        elif strategy == "vector":
            ranked = [(i, float(vector_scores[i])) for i in vector_rank]
        elif strategy == "hybrid":
            fused: dict[int, float] = {}
            for ranking in (bm25_rank, vector_rank):
                for rank, index in enumerate(ranking, 1):
                    fused[index] = fused.get(index, 0.0) + 1.0 / (rrf_k + rank)
            ranked = sorted(fused.items(), key=lambda item: (-item[1], self.chunks[item[0]].id))
        else:
            raise ValueError(f"unknown retrieval strategy: {strategy}")

        return [
            SearchHit(
                chunk_id=self.chunks[index].id,
                text=self.chunks[index].text,
                metadata=self.chunks[index].metadata,
                score=score,
                rank=rank,
                strategy=strategy,
            )
            for rank, (index, score) in enumerate(ranked[:top_k], 1)
        ]


class ParentDocumentRetriever:
    def __init__(self, child_retriever: HybridRetriever, parents: dict[str, IndexedChunk]) -> None:
        self.child_retriever = child_retriever
        self.parents = parents

    def search(self, query: str, *, top_k: int = 5, **kwargs) -> list[ExpandedSearchHit]:
        child_hits = self.child_retriever.search(query, top_k=top_k * 3, **kwargs)
        expanded: list[ExpandedSearchHit] = []
        seen: set[str] = set()
        for child in child_hits:
            parent_id = child.metadata.get("parent_id")
            parent = self.parents.get(parent_id) if parent_id else None
            if parent is None or parent_id in seen:
                continue
            if parent.metadata.get("version_id") != child.metadata.get("version_id"):
                continue
            seen.add(parent_id)
            expanded.append(
                ExpandedSearchHit(
                    parent=SearchHit(
                        chunk_id=parent.id,
                        text=parent.text,
                        metadata=parent.metadata,
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
