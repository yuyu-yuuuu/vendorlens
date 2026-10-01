from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from vendorlens.evaluation import retrieval_metrics
from vendorlens.retrieval import HybridRetriever, IndexedChunk


def run(dataset_path: Path, output_path: Path, top_k: int) -> dict:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    chunks = [
        IndexedChunk(item["id"], item["text"], item["metadata"]) for item in dataset["chunks"]
    ]
    retriever = HybridRetriever(chunks)
    results: dict[str, dict] = {}
    for strategy in ("bm25", "vector", "hybrid"):
        cases = []
        for query in dataset["queries"]:
            hits = retriever.search(
                query["text"],
                strategy=strategy,
                top_k=top_k,
                filters=query.get("filters"),
            )
            metrics = retrieval_metrics(
                [hit.chunk_id for hit in hits], set(query["relevant"]), top_k
            )
            cases.append(
                {
                    "query_id": query["id"],
                    "ranked_ids": [hit.chunk_id for hit in hits],
                    "metrics": asdict(metrics),
                }
            )
        keys = ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k")
        aggregate = {key: sum(case["metrics"][key] for case in cases) / len(cases) for key in keys}
        results[strategy] = {"aggregate": aggregate, "cases": cases}

    report = {
        "dataset_version": dataset["dataset_version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "top_k": top_k,
        "embedding": "deterministic-hash-256",
        "strategies": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reproducible retrieval baselines")
    parser.add_argument("--dataset", type=Path, default=Path("evals/retrieval_dataset.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/evals/retrieval-baseline.json")
    )
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    report = run(args.dataset, args.output, args.top_k)
    for strategy, result in report["strategies"].items():
        metrics = result["aggregate"]
        print(
            f"{strategy:>6}  Recall@{args.top_k}={metrics['recall_at_k']:.3f}  "
            f"Precision@{args.top_k}={metrics['precision_at_k']:.3f}  "
            f"MRR={metrics['mrr']:.3f}  NDCG={metrics['ndcg_at_k']:.3f}"
        )


if __name__ == "__main__":
    main()
