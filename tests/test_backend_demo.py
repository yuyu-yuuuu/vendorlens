from vendorlens.backend_demo import run_demo


def test_offline_demo_covers_storage_indexing_review_persistence_and_recommendation(tmp_path):
    result = run_demo(tmp_path / "demo")
    assert result["paid_model_calls"] == 0
    assert result["vendors"] == 3
    assert result["report"]["executive_summary"] == "推荐：alpha。"
    assert (tmp_path / "demo" / "result.json").exists()
