import pytest

from vendorlens.cost_grounding import (
    CostGroundingError,
    extract_grounded_costs,
    validate_cost_numbers,
)
from vendorlens.evidence import source_hash
from vendorlens.schemas import EvidenceItem


def ev(text, *, item_id="ev-1", parent_id="parent-1", quote=None):
    return EvidenceItem(
        id=item_id,
        project_id="p",
        vendor_id="v",
        document_version_id="ver",
        chunk_id=parent_id,
        context_text=text,
        quote=quote or text,
        source_text_hash=source_hash(text),
        location={"page": 1},
    )


def test_monthly_annual_optional_and_derived_tco_have_exact_source_spans():
    text = " 实施费用80万元，月费1,000元，可选培训5万元。"
    costs = extract_grounded_costs([ev(text)], "CNY")
    assert costs.totals() == {"one_time": 800000, "recurring": 12000, "optional": 50000}
    result = costs.result(vendor_id="v", currency="CNY", comparison_months=18)
    assert result["total_cost_of_ownership"] == 818000
    assert all(text[item.start : item.end] == item.source_text for item in costs.items)
    validate_cost_numbers(
        costs, one_time=800000, recurring=12000, optional=50000, tco=818000, comparison_months=18
    )


def test_parent_context_is_used_and_repeated_child_hits_do_not_duplicate_fees():
    text = "实施费用80万元。年度维保10万元。"
    costs = extract_grounded_costs(
        [
            ev(text, quote="实施费用80万元。"),
            ev(text, quote="年度维保10万元。", item_id="ev-2"),
        ],
        "CNY",
    )
    assert len(costs.items) == 2
    assert costs.totals()["recurring"] == 100000


def test_distinct_occurrences_with_same_amount_are_not_deduplicated():
    text = "软件费用1万元；实施费用1万元。"
    assert extract_grounded_costs([ev(text)], "CNY").totals()["one_time"] == 20000


@pytest.mark.parametrize(
    "text",
    [
        "每年1万元每月1千元",
        "实施1万美元",
        "一次费用-100元",
        "每季度100元",
        "3年费用10万元",
        "可选每年100元",
        "总价10万元",
        "软件80万元及实施10万元",
        "约80万元",
        "每人100元",
        "未税100元",
        "折扣100元",
        "USD 100元",
    ],
)
def test_ambiguous_or_incompatible_costs_do_not_become_authoritative_inputs(text):
    costs = extract_grounded_costs([ev(text)], "CNY")
    assert not costs.items
    assert costs.missing
    with pytest.raises(CostGroundingError, match="cost_source_missing"):
        validate_cost_numbers(costs, one_time=100, recurring=0, optional=0)


def test_legacy_evidence_uses_only_quote_and_does_not_invent_context():
    item = ev("实施80万元。年度维保10万元。", quote="实施80万元。")
    legacy = item.model_copy(update={"context_text": None})
    assert extract_grounded_costs([legacy], "CNY").totals()["recurring"] == 0


def test_fake_tco_and_category_swapping_are_rejected():
    costs = extract_grounded_costs([ev("年度维保10万元")], "CNY")
    with pytest.raises(CostGroundingError, match="one_time"):
        validate_cost_numbers(costs, one_time=100000, recurring=0, optional=0)
    with pytest.raises(CostGroundingError, match="derived_tco_mismatch"):
        validate_cost_numbers(costs, one_time=0, recurring=100000, optional=0, tco=999999)


@pytest.mark.parametrize("text", ["实施费用1e6元", "实施费用1/2万元"])
def test_scientific_or_fraction_amounts_do_not_authorize_partial_digits(text):
    costs = extract_grounded_costs([ev(text)], "CNY")
    assert costs.items == []
    assert costs.missing


def test_same_cited_evidence_id_twice_does_not_duplicate_costs():
    item = ev("实施费用1万元")
    assert extract_grounded_costs([item, item], "CNY").totals()["one_time"] == 10000


def test_required_categories_need_nonoptional_grounded_costs():
    costs = extract_grounded_costs([ev("软件许可及实施一次性80万元；可选维保每年1万元")], "CNY")
    costs.require_categories(["software", "implementation", "maintenance"])
    assert any("maintenance" in message for message in costs.missing)
    assert not any(
        "software" in message or "implementation" in message for message in costs.missing
    )
