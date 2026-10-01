from __future__ import annotations

import re
from collections import defaultdict

from vendorlens.retrieval import tokenize
from vendorlens.schemas import (
    CommitmentAssessment,
    CommitmentFinding,
    CommitmentStatus,
    CostAssessment,
    DomainAssessment,
    DomainAssessmentStatus,
    EvidenceItem,
    RequirementAssessment,
    RequirementItem,
    RequirementStatus,
    ReviewDomain,
)

DOMAIN_TOOL_ALLOWLISTS = {
    ReviewDomain.REQUIREMENT_COMPLIANCE: frozenset(
        {"search_requirement_evidence", "compare_requirement_response"}
    ),
    ReviewDomain.COST_MODELING: frozenset(
        {"extract_cost_items", "normalize_cost", "calculate_tco"}
    ),
    ReviewDomain.COMMITMENT_VERIFICATION: frozenset(
        {"extract_commitments", "compare_milestones", "check_dependencies"}
    ),
}


class RequirementRuleEvaluator:
    negative_markers = ("不支持", "无法支持", "不满足", "不提供", "不具备")
    positive_markers = ("支持", "满足", "符合", "提供", "具备")

    def evaluate(
        self,
        vendor_id: str,
        requirements: list[RequirementItem],
        evidence_by_requirement: dict[str, list[EvidenceItem]],
    ) -> DomainAssessment:
        if not requirements:
            return DomainAssessment(
                domain=ReviewDomain.REQUIREMENT_COMPLIANCE,
                vendor_id=vendor_id,
                status=DomainAssessmentStatus.FAILED,
                missing_information=["未提供需求目录"],
            )
        findings: list[RequirementAssessment] = []
        all_evidence_ids: list[str] = []
        for requirement in requirements:
            evidence = evidence_by_requirement.get(requirement.id, [])
            quotes = " ".join(item.quote for item in evidence)
            status = self._status(requirement.text, quotes)
            evidence_ids = (
                [item.id for item in evidence] if status is not RequirementStatus.NOT_STATED else []
            )
            all_evidence_ids.extend(evidence_ids)
            findings.append(
                RequirementAssessment(
                    vendor_id=vendor_id,
                    requirement_id=requirement.id,
                    mandatory=requirement.mandatory,
                    weight=requirement.weight,
                    status=status,
                    explanation="由确定性降级规则根据明确响应生成",
                    evidence_ids=evidence_ids,
                    confidence=1.0
                    if status in (RequirementStatus.MET, RequirementStatus.NOT_MET)
                    else 0.5,
                )
            )
        return DomainAssessment(
            domain=ReviewDomain.REQUIREMENT_COMPLIANCE,
            vendor_id=vendor_id,
            status=DomainAssessmentStatus.DEGRADED,
            requirement_findings=findings,
            missing_information=[
                item.requirement_id
                for item in findings
                if item.status is RequirementStatus.NOT_STATED
            ],
            evidence_ids=list(dict.fromkeys(all_evidence_ids)),
        )

    def _status(self, requirement: str, quotes: str) -> RequirementStatus:
        if not quotes.strip():
            return RequirementStatus.NOT_STATED
        if any(marker in quotes for marker in self.negative_markers):
            return RequirementStatus.NOT_MET
        requirement_tokens = set(tokenize(requirement))
        quote_tokens = set(tokenize(quotes))
        overlap = len(requirement_tokens & quote_tokens) / max(len(requirement_tokens), 1)
        if any(marker in quotes for marker in self.positive_markers) and overlap >= 0.2:
            return RequirementStatus.MET
        if overlap >= 0.2:
            return RequirementStatus.PARTIALLY_MET
        return RequirementStatus.NOT_STATED


class DeterministicCostEngine:
    amount_pattern = re.compile(r"(?<![\d.])([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(万)?\s*元")

    def evaluate(
        self,
        vendor_id: str,
        evidence: list[EvidenceItem],
        *,
        currency: str = "CNY",
        comparison_months: int = 36,
    ) -> DomainAssessment:
        if not evidence:
            return DomainAssessment(
                domain=ReviewDomain.COST_MODELING,
                vendor_id=vendor_id,
                status=DomainAssessmentStatus.FAILED,
                missing_information=["未提供成本证据"],
            )
        one_time = 0.0
        recurring = 0.0
        optional = 0.0
        matched_ids: list[str] = []
        for item in evidence:
            values = [self._amount(match) for match in self.amount_pattern.finditer(item.quote)]
            if not values:
                continue
            matched_ids.append(item.id)
            value = sum(values)
            if any(marker in item.quote for marker in ("可选", "选配", "额外购买")):
                optional += value
            elif any(marker in item.quote for marker in ("每年", "年费", "年度", "/年")):
                recurring += value
            else:
                one_time += value
        missing = [] if matched_ids else ["未找到明确金额"]
        assessment = CostAssessment(
            vendor_id=vendor_id,
            currency=currency,
            comparison_months=comparison_months,
            one_time_cost=one_time,
            recurring_cost=recurring,
            optional_cost=optional,
            total_cost_of_ownership=one_time + recurring * comparison_months / 12,
            missing_costs=missing,
            evidence_ids=matched_ids or [item.id for item in evidence],
        )
        return DomainAssessment(
            domain=ReviewDomain.COST_MODELING,
            vendor_id=vendor_id,
            status=DomainAssessmentStatus.DEGRADED,
            cost_assessment=assessment,
            missing_information=missing,
            evidence_ids=matched_ids,
        )

    @staticmethod
    def _amount(match: re.Match) -> float:
        value = float(match.group(1).replace(",", ""))
        return value * 10_000 if match.group(2) else value


class CommitmentRuleChecker:
    categories = {
        "delivery": ("交付", "上线", "工期", "周期"),
        "staffing": ("人员", "人天", "项目经理", "工程师"),
        "acceptance": ("验收",),
        "sla": ("SLA", "可用性", "响应时间"),
        "warranty": ("质保", "维保"),
        "dependency": ("前提", "依赖", "客户提供", "甲方提供"),
    }

    def evaluate(self, vendor_id: str, evidence: list[EvidenceItem]) -> DomainAssessment:
        grouped: dict[str, list[EvidenceItem]] = defaultdict(list)
        for item in evidence:
            for category, markers in self.categories.items():
                if any(marker.lower() in item.quote.lower() for marker in markers):
                    grouped[category].append(item)

        findings: list[CommitmentFinding] = []
        conflicts: list[str] = []
        for category, items in grouped.items():
            normalized_quotes = list(dict.fromkeys(item.quote.strip() for item in items))
            conflicting = len(normalized_quotes) > 1 and self._has_distinct_numbers(
                normalized_quotes
            )
            if conflicting:
                conflicts.append(f"{category}: {' | '.join(normalized_quotes)}")
            findings.append(
                CommitmentFinding(
                    category=category,
                    statement="；".join(normalized_quotes),
                    status=(
                        CommitmentStatus.CONFLICTING if conflicting else CommitmentStatus.VERIFIED
                    ),
                    evidence_ids=[item.id for item in items],
                )
            )
        if not findings:
            findings.append(
                CommitmentFinding(
                    category="general",
                    statement="材料未明确说明可核验承诺",
                    status=CommitmentStatus.NOT_STATED,
                )
            )
        assessment = CommitmentAssessment(
            vendor_id=vendor_id,
            findings=findings,
            missing_information=[] if grouped else ["未找到明确承诺"],
            conflicts=conflicts,
        )
        return DomainAssessment(
            domain=ReviewDomain.COMMITMENT_VERIFICATION,
            vendor_id=vendor_id,
            status=DomainAssessmentStatus.DEGRADED,
            commitment_assessment=assessment,
            missing_information=assessment.missing_information,
            conflicts=conflicts,
            evidence_ids=list(
                dict.fromkeys(item.id for items in grouped.values() for item in items)
            ),
        )

    @staticmethod
    def _has_distinct_numbers(quotes: list[str]) -> bool:
        numbers = {tuple(re.findall(r"\d+(?:\.\d+)?", quote)) for quote in quotes}
        return len(numbers) > 1
