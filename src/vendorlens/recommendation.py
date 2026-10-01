from __future__ import annotations

from collections import defaultdict

from vendorlens.schemas import (
    CommitmentAssessment,
    CommitmentStatus,
    CostAssessment,
    RecommendationLevel,
    RecommendationResult,
    RequirementAssessment,
    RequirementStatus,
    ReviewWeights,
    VendorRecommendationScore,
)

REQUIREMENT_POINTS = {
    RequirementStatus.MET: 100.0,
    RequirementStatus.PARTIALLY_MET: 50.0,
    RequirementStatus.NOT_MET: 0.0,
    RequirementStatus.NOT_STATED: 0.0,
}

COMMITMENT_POINTS = {
    CommitmentStatus.VERIFIED: 100.0,
    CommitmentStatus.PARTIAL: 50.0,
    CommitmentStatus.CONFLICTING: 0.0,
    CommitmentStatus.NOT_STATED: 0.0,
}


def _requirement_score(items: list[RequirementAssessment]) -> float:
    total_weight = sum(item.weight for item in items)
    return sum(REQUIREMENT_POINTS[item.status] * item.weight for item in items) / total_weight


def _commitment_score(item: CommitmentAssessment | None) -> float:
    if item is None or not item.findings:
        return 0.0
    return sum(COMMITMENT_POINTS[finding.status] for finding in item.findings) / len(item.findings)


def _cost_scores(costs: dict[str, CostAssessment]) -> dict[str, float]:
    complete = [item.total_cost_of_ownership for item in costs.values() if item.complete]
    if not complete:
        return {vendor_id: 0.0 for vendor_id in costs}
    minimum = min(complete)
    scores: dict[str, float] = {}
    for vendor_id, item in costs.items():
        if not item.complete:
            scores[vendor_id] = 0.0
        elif item.total_cost_of_ownership == 0:
            scores[vendor_id] = 100.0 if minimum == 0 else 0.0
        elif minimum == 0:
            scores[vendor_id] = 0.0
        else:
            scores[vendor_id] = minimum / item.total_cost_of_ownership * 100
    return scores


def build_recommendation(
    *,
    requirements: list[RequirementAssessment],
    costs: list[CostAssessment],
    commitments: list[CommitmentAssessment],
    weights: ReviewWeights | None = None,
    degraded_vendor_ids: set[str] | None = None,
) -> RecommendationResult:
    weights = weights or ReviewWeights()
    degraded_vendor_ids = degraded_vendor_ids or set()
    requirements_by_vendor: dict[str, list[RequirementAssessment]] = defaultdict(list)
    for item in requirements:
        requirements_by_vendor[item.vendor_id].append(item)
    costs_by_vendor = {item.vendor_id: item for item in costs}
    commitments_by_vendor = {item.vendor_id: item for item in commitments}
    vendor_ids = sorted(
        set(requirements_by_vendor) | set(costs_by_vendor) | set(commitments_by_vendor)
    )
    relative_cost_scores = _cost_scores(costs_by_vendor)
    vendor_scores: dict[str, VendorRecommendationScore] = {}

    for vendor_id in vendor_ids:
        vendor_requirements = requirements_by_vendor[vendor_id]
        vendor_cost = costs_by_vendor.get(vendor_id)
        vendor_commitments = commitments_by_vendor.get(vendor_id)
        reasons = [
            f"mandatory requirement {item.requirement_id} is not met"
            for item in vendor_requirements
            if item.mandatory and item.status is RequirementStatus.NOT_MET
        ]
        conditions: list[str] = []
        if vendor_id in degraded_vendor_ids:
            conditions.append(f"{vendor_id}: one or more domain assessments are degraded")
        if not vendor_requirements:
            conditions.append(f"{vendor_id}: missing requirement assessment")
        elif any(
            item.status in (RequirementStatus.PARTIALLY_MET, RequirementStatus.NOT_STATED)
            for item in vendor_requirements
        ):
            conditions.append(f"{vendor_id}: requirements are partially met or not stated")
        if vendor_cost is None:
            conditions.append(f"{vendor_id}: missing cost assessment")
        else:
            conditions.extend(
                f"{vendor_id}: missing cost {item}" for item in vendor_cost.missing_costs
            )
            conditions.extend(
                f"{vendor_id}: cost conflict {item}" for item in vendor_cost.conflicts
            )
        if vendor_commitments is None:
            conditions.append(f"{vendor_id}: missing commitment assessment")
        else:
            if not vendor_commitments.complete:
                conditions.append(f"{vendor_id}: commitments are incomplete or conflicting")
            conditions.extend(
                f"{vendor_id}: {item}" for item in vendor_commitments.missing_information
            )
            conditions.extend(f"{vendor_id}: {item}" for item in vendor_commitments.conflicts)

        requirement_score = _requirement_score(vendor_requirements) if vendor_requirements else 0.0
        cost_score = relative_cost_scores.get(vendor_id, 0.0)
        commitment_score = _commitment_score(vendor_commitments)
        total_score = (
            requirement_score * weights.requirement_compliance
            + cost_score * weights.cost_competitiveness
            + commitment_score * weights.commitment
        )
        vendor_scores[vendor_id] = VendorRecommendationScore(
            vendor_id=vendor_id,
            requirement_score=requirement_score,
            cost_score=cost_score,
            commitment_score=commitment_score,
            total_score=total_score,
            eligible=bool(vendor_requirements) and not reasons,
            disqualified=bool(reasons),
            disqualification_reasons=reasons,
            conditions=conditions,
        )

    eligible = [item for item in vendor_scores.values() if item.eligible]
    if not eligible:
        return RecommendationResult(
            level=RecommendationLevel.UNABLE_TO_RECOMMEND,
            vendor_scores=vendor_scores,
            conditions=["no vendor has a valid requirement assessment"],
        )

    top_score = max(item.total_score for item in eligible)
    winners = sorted(
        item.vendor_id for item in eligible if abs(item.total_score - top_score) <= 1e-9
    )
    winner_conditions = [
        condition for vendor_id in winners for condition in vendor_scores[vendor_id].conditions
    ]
    level = (
        RecommendationLevel.CONDITIONALLY_RECOMMENDED
        if winner_conditions
        else RecommendationLevel.RECOMMENDED
    )
    return RecommendationResult(
        level=level,
        recommended_vendor_ids=winners,
        vendor_scores=vendor_scores,
        conditions=winner_conditions,
    )
