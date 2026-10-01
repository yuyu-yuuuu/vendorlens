import pytest

from vendorlens.evaluation import retrieval_metrics
from vendorlens.retrieval import HybridRetriever, IndexedChunk, ParentDocumentRetriever


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


def test_parent_retrieval_deduplicates_parent_and_keeps_winning_child():
    children = [
        IndexedChunk("c1", "总报价 88 万元", {"parent_id": "p1", "version_id": "v1"}),
        IndexedChunk("c2", "预付款比例 30%", {"parent_id": "p1", "version_id": "v1"}),
        IndexedChunk("c3", "总报价 82 万元", {"parent_id": "p2", "version_id": "v2"}),
    ]
    parents = {
        "p1": IndexedChunk("p1", "报价 88 万，预付款 30%。", {"version_id": "v1"}),
        "p2": IndexedChunk("p2", "新版报价 82 万。", {"version_id": "v2"}),
    }
    retriever = ParentDocumentRetriever(HybridRetriever(children), parents)

    hits = retriever.search("88 万元预付款", top_k=2, filters={"version_id": "v1"})

    assert len(hits) == 1
    assert hits[0].parent.chunk_id == "p1"
    assert hits[0].matched_child.chunk_id in {"c1", "c2"}
    assert hits[0].parent.metadata["version_id"] == "v1"
