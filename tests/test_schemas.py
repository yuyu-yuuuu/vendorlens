import pytest
from pydantic import ValidationError

from vendorlens.schemas import PlanDimension, ResearchPlan, ResearchSubtask


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
