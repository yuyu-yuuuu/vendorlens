import numpy as np
import pytest

from vendorlens.domain import SearchHit
from vendorlens.indexes import PersistentHybridRetriever
from vendorlens.retrieval_runtime import LocalCrossEncoder, LocalSentenceEmbedder


def test_local_models_use_provided_weights_without_network(tmp_path):
    class Model:
        def encode(self, texts, normalize_embeddings):
            assert normalize_embeddings
            return np.array([[1, 0] for _ in texts])

        def predict(self, pairs):
            return [0.1, 0.9]

    assert LocalSentenceEmbedder(tmp_path, model=Model()).embed(["中文"]) == [[1, 0]]
    hits = [SearchHit(str(i), text, {}, 1, i + 1, "hybrid") for i, text in enumerate(["甲", "乙"])]
    ranked = LocalCrossEncoder(tmp_path, model=Model()).rank("查询", hits)
    assert ranked[0].chunk_id == "1" and ranked[0].strategy == "reranked"
    with pytest.raises(ValueError, match="directory"):
        LocalSentenceEmbedder(tmp_path / "missing", model=Model())


def test_reranker_failure_preserves_filtered_fused_candidates():
    class Index:
        def search(self, *args, **kwargs):
            assert kwargs["filters"] == {"project_id": "p"}
            return [SearchHit("allowed", "原文", {"project_id": "p"}, 1, 1, "hybrid")]

    class Broken:
        def rank(self, *args):
            raise RuntimeError("ranker unavailable")

    hits = PersistentHybridRetriever(Index(), Index(), Broken()).search(
        "查询", filters={"project_id": "p"}, top_k=1
    )
    assert [hit.chunk_id for hit in hits] == ["allowed"]
