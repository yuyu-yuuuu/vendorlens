import json

from vendorlens.eval_cli import run


def test_retrieval_eval_writes_all_strategy_results(tmp_path):
    dataset = {
        "dataset_version": "test",
        "chunks": [
            {"id": "a", "text": "报价 80 万元", "metadata": {"project": "p"}},
            {"id": "b", "text": "交付 30 天", "metadata": {"project": "p"}},
        ],
        "queries": [
            {
                "id": "q",
                "text": "报价",
                "relevant": ["a"],
                "filters": {"project": "p"},
            }
        ],
    }
    dataset_path = tmp_path / "dataset.json"
    output_path = tmp_path / "result.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    report = run(dataset_path, output_path, 1)

    assert set(report["strategies"]) == {"bm25", "vector", "hybrid"}
    assert output_path.exists()
    assert report["strategies"]["hybrid"]["aggregate"]["recall_at_k"] == 1.0
