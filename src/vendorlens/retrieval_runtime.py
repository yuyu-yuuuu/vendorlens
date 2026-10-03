"""One retrieval configuration shared by ingestion, API and Worker."""

from __future__ import annotations

import hashlib
from functools import lru_cache

from vendorlens.config import Settings, get_settings
from vendorlens.indexes import ChromaVectorIndex, DocumentIndexingService, PersistentBM25Index
from vendorlens.retrieval import HashEmbeddingProvider


class LocalSentenceEmbedder:
    def __init__(self, path, *, model=None):
        if not path or not path.is_dir():
            raise ValueError("a local embedding model directory is required")
        if model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ValueError("install the retrieval extra for local models") from None
            model = SentenceTransformer(str(path), local_files_only=True)
        self.model = model

    def embed(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()


class LocalCrossEncoder:
    def __init__(self, path, *, model=None):
        if not path or not path.is_dir():
            raise ValueError("a local reranker directory is required")
        if model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError:
                raise ValueError("install the retrieval extra for local models") from None
            model = CrossEncoder(str(path), local_files_only=True)
        self.model = model

    def rank(self, query, hits):
        import math
        from dataclasses import replace

        if not hits:
            return []
        scores = list(self.model.predict([(query, hit.text) for hit in hits]))
        if len(scores) != len(hits) or not all(math.isfinite(float(s)) for s in scores):
            raise ValueError("invalid reranker scores")
        ordered = sorted(
            zip(hits, scores, strict=True), key=lambda p: (-float(p[1]), p[0].chunk_id)
        )
        return [
            replace(hit, score=float(score), rank=rank, strategy="reranked")
            for rank, (hit, score) in enumerate(ordered, 1)
        ]


@lru_cache(maxsize=4)
def _embedder(provider, path):
    return HashEmbeddingProvider() if provider == "hash" else LocalSentenceEmbedder(path)


@lru_cache(maxsize=4)
def _reranker(path):
    return LocalCrossEncoder(path)


def retrieval_components(settings: Settings):
    embedder = _embedder(settings.embedding_provider, settings.embedding_model_path)
    identity = f"{settings.embedding_provider}:{settings.embedding_model_path or '256'}"
    collection = (
        "vendorlens_chunks"
        if settings.embedding_provider == "hash"
        else ("vendorlens_" + hashlib.sha256(identity.encode()).hexdigest()[:16])
    )
    bm25 = PersistentBM25Index(settings.storage_root / "indexes" / "bm25.json")
    vector = ChromaVectorIndex.from_url(
        settings.chroma_url, collection_name=collection, embedder=embedder
    )
    reranker = _reranker(settings.reranker_model_path) if settings.reranker_model_path else None
    return bm25, vector, reranker


def get_indexing_service():
    class LazyIndexer:
        def index_version(self, session, version_id):
            bm25, vector, _ = retrieval_components(get_settings())
            return DocumentIndexingService(bm25, vector).index_version(session, version_id)

    return LazyIndexer()
