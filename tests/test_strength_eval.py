from vendorlens.semantic_rules import requirement_response
from vendorlens.strength_eval import classification, timings


def test_macro_f1_includes_missing_class_and_reports_false_positive():
    result = classification(
        [{"gold": "yes", "prediction": "yes"}, {"gold": "no", "prediction": "yes"}], ["yes", "no"]
    )
    assert result["accuracy"] == 0.5
    assert result["per_class"]["yes"]["precision"] == 0.5
    assert result["per_class"]["no"]["f1"] == 0
    assert abs(result["macro_f1"] - 1 / 3) < 1e-9


def test_nearest_rank_p95_and_scoped_negation_with_parent_restrictions():
    assert timings(list(range(1, 21)))["p95_ms"] == 19
    assert (
        requirement_response("支持私有化部署", "不支持离线运行，但支持私有化部署。")["status"]
        == "met"
    )
    result = requirement_response(
        "支持私有化部署", "支持私有化部署。该能力仅限企业版，需另行购买。"
    )
    assert result["status"] == "partially_met"
    assert result["limitations"]


def test_unanswered_requirement_and_numeric_or_edition_denial_are_distinct():
    assert (
        requirement_response("支持日志归档", "日志归档待确认，尚未提供方案。")["status"]
        == "not_stated"
    )
    assert requirement_response("响应时间不超过3秒", "响应时间为4秒。")["status"] == "not_met"
    assert (
        requirement_response("基础版支持日志归档", "日志归档仅限企业版，基础版不支持。")["status"]
        == "not_met"
    )
    assert requirement_response("支持网络断开运行", "网络断开运行不可用。")["status"] == "not_met"
