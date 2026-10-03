import json

import pytest

from vendorlens.challenge_eval import cost_metrics
from vendorlens.metrics_report import generate


def test_cost_diagnostics_keep_wrong_answers_and_abstentions_in_separate_denominators():
    rows = [
        {"id": "correct", "gold_tco": 10, "prediction_tco": 10, "passed": True},
        {"id": "wrong", "gold_tco": 20, "prediction_tco": 5, "passed": False},
        {"id": "unsupported", "gold_tco": 30, "prediction_tco": None, "passed": False},
        {"id": "abstain", "gold_tco": None, "prediction_tco": None, "passed": True},
        {"id": "subtotal", "gold_tco": None, "prediction_tco": 5, "passed": False},
    ]
    result = cost_metrics(rows)
    assert result["numeric_coverage"] == 2 / 3
    assert result["answer_exact_accuracy"] == 1 / 2
    assert result["known_tco_exact_accuracy"] == 1 / 3
    assert result["unknown_correct_abstentions"] == 1
    assert result["unknown_numeric_outputs"] == ["subtotal"]
    assert result["wrong_numeric_outputs"] == ["wrong"]


def test_template_regression_cannot_regenerate_resume_effect_claims(tmp_path):
    (tmp_path / "metrics.json").write_text(json.dumps({"dataset_version": "old-template"}))
    with pytest.raises(ValueError, match="template regression"):
        generate(tmp_path, tmp_path / "report.md", tmp_path / "resume.txt")
    assert not (tmp_path / "resume.txt").exists()
