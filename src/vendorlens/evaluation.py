from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_k: float
    precision_at_k: float
    mrr: float
    ndcg_at_k: float


def retrieval_metrics(ranked_ids: list[str], relevant_ids: set[str], k: int) -> RetrievalMetrics:
    top = ranked_ids[:k]
    hits = [1 if item in relevant_ids else 0 for item in top]
    recall = sum(hits) / len(relevant_ids) if relevant_ids else 0.0
    precision = sum(hits) / k if k else 0.0
    reciprocal_rank = next((1.0 / rank for rank, hit in enumerate(hits, 1) if hit), 0.0)
    dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(hits, 1))
    ideal_hits = [1] * min(len(relevant_ids), k)
    idcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(ideal_hits, 1))
    ndcg = dcg / idcg if idcg else 0.0
    return RetrievalMetrics(recall, precision, reciprocal_rank, ndcg)

