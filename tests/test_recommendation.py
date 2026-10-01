import pytest
from pydantic import ValidationError

from vendorlens.recommendation import build_recommendation
from vendorlens.schemas import (
    CommitmentAssessment,
    CommitmentFinding,
    CommitmentStatus,
    CostAssessment,
    RecommendationLevel,
    RequirementAssessment,
    RequirementStatus,
    ReviewWeights,
)


def requirement(
    vendor_id: str,
    requirement_id: str,
    status: RequirementStatus,
    *,
    mandatory: bool = False,
    weight: float = 1.0,
) -> RequirementAssessment:
    evidence_ids = [] if status is RequirementStatus.NOT_STATED else [f"ev-{vendor_id}"]
    return RequirementAssessment(
        vendor_id=vendor_id,
        requirement_id=requirement_id,
        mandatory=mandatory,
        weight=weight,
        status=status,
        explanation="测试结论",
        evidence_ids=evidence_ids,
        confidence=0.9,
    )


def cost(vendor_id: str, tco: float, *, missing: list[str] | None = None):
    return CostAssessment(
        vendor_id=vendor_id,
        currency="CNY",
        comparison_months=36,
        one_time_cost=tco,
        recurring_cost=0,
        optional_cost=0,
        total_cost_of_ownership=tco,
        missing_costs=missing or [],
        evidence_ids=[f"cost-{vendor_id}"],
    )


def commitments(vendor_id: str, status: CommitmentStatus = CommitmentStatus.VERIFIED):
    evidence_ids = [] if status is CommitmentStatus.NOT_STATED else [f"commit-{vendor_id}"]
    return CommitmentAssessment(
        vendor_id=vendor_id,
        findings=[
            CommitmentFinding(
                category="delivery_date",
                statement="在 90 天内交付",
                status=status,
                evidence_ids=evidence_ids,
            )
        ],
    )


def test_review_weights_must_sum_to_one():
    with pytest.raises(ValidationError, match="weights must sum to 1"):
        ReviewWeights(requirement_compliance=0.5, cost_competitiveness=0.4, commitment=0.2)


def test_supported_requirement_requires_evidence():
    with pytest.raises(ValidationError, match="evidence"):
        RequirementAssessment(
            vendor_id="vendor-a",
            requirement_id="req-1",
            mandatory=False,
            status=RequirementStatus.MET,
            explanation="满足",
            confidence=0.9,
        )


def test_lowest_price_cannot_override_mandatory_requirement_failure():
    result = build_recommendation(
        requirements=[
            requirement("vendor-a", "req-1", RequirementStatus.MET, mandatory=True),
            requirement("vendor-b", "req-1", RequirementStatus.NOT_MET, mandatory=True),
        ],
        costs=[cost("vendor-a", 100), cost("vendor-b", 50)],
        commitments=[commitments("vendor-a"), commitments("vendor-b")],
    )

    assert result.level is RecommendationLevel.RECOMMENDED
    assert result.recommended_vendor_ids == ["vendor-a"]
    assert result.vendor_scores["vendor-b"].disqualified is True
    assert "req-1" in result.vendor_scores["vendor-b"].disqualification_reasons[0]


def test_incomplete_cost_produces_only_conditional_recommendation():
    result = build_recommendation(
        requirements=[requirement("vendor-a", "req-1", RequirementStatus.MET)],
        costs=[cost("vendor-a", 100, missing=["第三年维保费"])],
        commitments=[commitments("vendor-a")],
    )

    assert result.level is RecommendationLevel.CONDITIONALLY_RECOMMENDED
    assert result.recommended_vendor_ids == ["vendor-a"]
    assert result.vendor_scores["vendor-a"].cost_score == 0
    assert "第三年维保费" in result.conditions[0]


def test_requirement_and_commitment_scores_use_documented_values():
    result = build_recommendation(
        requirements=[
            requirement("vendor-a", "req-1", RequirementStatus.MET, weight=3),
            requirement("vendor-a", "req-2", RequirementStatus.PARTIALLY_MET, weight=1),
        ],
        costs=[cost("vendor-a", 100)],
        commitments=[commitments("vendor-a", CommitmentStatus.PARTIAL)],
    )

    score = result.vendor_scores["vendor-a"]
    assert score.requirement_score == pytest.approx(87.5)
    assert score.cost_score == pytest.approx(100)
    assert score.commitment_score == pytest.approx(50)
    assert score.total_score == pytest.approx(83.75)
    assert result.level is RecommendationLevel.CONDITIONALLY_RECOMMENDED


def test_exact_top_score_tie_returns_joint_recommendation():
    result = build_recommendation(
        requirements=[
            requirement("vendor-a", "req-1", RequirementStatus.MET),
            requirement("vendor-b", "req-1", RequirementStatus.MET),
        ],
        costs=[cost("vendor-a", 100), cost("vendor-b", 100)],
        commitments=[commitments("vendor-a"), commitments("vendor-b")],
    )

    assert result.level is RecommendationLevel.RECOMMENDED
    assert result.recommended_vendor_ids == ["vendor-a", "vendor-b"]


def test_degraded_assessment_cannot_produce_unconditional_recommendation():
    result = build_recommendation(
        requirements=[requirement("vendor-a", "req-1", RequirementStatus.MET)],
        costs=[cost("vendor-a", 100)],
        commitments=[commitments("vendor-a")],
        degraded_vendor_ids={"vendor-a"},
    )

    assert result.level is RecommendationLevel.CONDITIONALLY_RECOMMENDED
    assert "degraded" in result.conditions[0]
