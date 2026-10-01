import pytest

from vendorlens.evaluation import retrieval_metrics
from vendorlens.retrieval import HybridRetriever, IndexedChunk


@pytest.fixture
def retriever():
    return HybridRetriever(
        [
            IndexedChunk("a", "甲供应商总报价 88 万元，交付周期 45 天", {"project": "p1"}),
            IndexedChunk("b", "乙供应商提供三年运维服务", {"project": "p1"}),
            IndexedChunk("c", "会议纪要要求在六月完成一期上线", {"project": "p2"}),
        ]
    )


@pytest.mark.parametrize("strategy", ["bm25", "vector", "hybrid"])
def test_all_retrieval_strategies_find_exact_quote(retriever, strategy):
    hits = retriever.search("甲供应商报价和交付周期", strategy=strategy, top_k=2)
    assert hits[0].chunk_id == "a"
    assert hits[0].strategy == strategy


def test_metadata_filter_is_enforced(retriever):
    hits = retriever.search("六月上线", filters={"project": "p1"}, top_k=5)
    assert {hit.chunk_id for hit in hits} == {"a", "b"}


def test_metrics_are_reproducible():
    metrics = retrieval_metrics(["a", "x", "b"], {"a", "b"}, 3)
    assert metrics.recall_at_k == 1.0
    assert metrics.precision_at_k == pytest.approx(2 / 3)
    assert metrics.mrr == 1.0
    assert metrics.ndcg_at_k == pytest.approx(0.9197207891)

