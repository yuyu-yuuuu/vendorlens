import hashlib

import pytest
from pydantic import ValidationError

from vendorlens.parsers import parse_document
from vendorlens.schemas import EvidenceItem, PlanDimension, ResearchPlan, ResearchSubtask


@pytest.mark.parametrize("text", ["支持私有化部署。\n仅限企业版。", "Supported. Enterprise only."])
def test_child_quote_preserves_parent_whitespace(text):
    chunks = parse_document("方案.txt", text.encode())
    parent = chunks[0]
    children = [item for item in chunks if item.chunk_type == "child"]
    assert children
    assert all(item.text in parent.text for item in children)
    assert children[0].text == parent.text


def context_evidence(**overrides):
    context = "支持私有化部署。仅限企业版，需额外购买授权。"
    values = dict(
        id="evidence:parent-1",
        project_id="project-1",
        document_version_id="version-1",
        chunk_id="parent-1",
        matched_chunk_id="child-1",
        quote="支持私有化部署。",
        context_text=context,
        source_text_hash=hashlib.sha256(context.encode("utf-8")).hexdigest(),
        location={"page": 1},
    )
    return EvidenceItem(**(values | overrides))


def test_evidence_preserves_parent_context_and_child_quote():
    item = context_evidence()
    restored = EvidenceItem.model_validate_json(item.model_dump_json())
    assert restored == item
    assert "仅限企业版" in restored.context_text
    assert "仅限企业版" not in restored.quote
    assert restored.matched_chunk_id == "child-1"


def test_evidence_rejects_context_with_wrong_parent_hash():
    with pytest.raises(ValidationError, match="context hash"):
        context_evidence(context_text="被替换的父块")


def test_evidence_rejects_quote_outside_parent():
    with pytest.raises(ValidationError, match="quote must occur"):
        context_evidence(quote="所有版本免费支持")


def test_legacy_evidence_without_context_remains_readable():
    item = context_evidence().model_dump(exclude={"context_text", "matched_chunk_id"})
    restored = EvidenceItem.model_validate(item)
    assert restored.context_text is None
    assert restored.matched_chunk_id is None


def test_research_plan_requires_normalized_weights():
    with pytest.raises(ValidationError, match="weights must sum to 1"):
        ResearchPlan(
            objective="选择实施方案",
            dimensions=[PlanDimension(name="价格", weight=0.8, description="总价")],
            subtasks=[
                ResearchSubtask(
                    id="price", title="价格", queries=["报价"], dimension_names=["价格"]
                )
            ],
        )


def test_research_plan_rejects_unknown_dimension_reference():
    with pytest.raises(ValidationError, match="unknown dimension"):
        ResearchPlan(
            objective="选择实施方案",
            dimensions=[PlanDimension(name="价格", weight=1.0, description="总价")],
            subtasks=[
                ResearchSubtask(id="risk", title="风险", queries=["风险"], dimension_names=["交付"])
            ],
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_cost_fields_reject_non_finite_values(value):
    from vendorlens.schemas import CostAssessment

    with pytest.raises(ValidationError):
        CostAssessment(vendor_id="v", currency="CNY", comparison_months=36,
                       one_time_cost=value, recurring_cost=0, optional_cost=0,
                       total_cost_of_ownership=0, evidence_ids=["ev"])
