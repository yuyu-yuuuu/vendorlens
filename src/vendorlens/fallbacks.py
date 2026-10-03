from __future__ import annotations

import re
from collections import defaultdict

from vendorlens.cost_grounding import extract_grounded_costs
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
            quotes = " ".join(item.context_text or item.quote for item in evidence)
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
        from vendorlens.semantic_rules import requirement_response

        return RequirementStatus(requirement_response(requirement, quotes)["status"])


class DeterministicCostEngine:
    def evaluate(
        self,
        vendor_id: str,
        evidence: list[EvidenceItem],
        *,
        currency: str = "CNY",
        comparison_months: int = 36,
        required_cost_categories: list[str] | None = None,
    ) -> DomainAssessment:
        grounded = extract_grounded_costs(evidence, currency).require_categories(
            required_cost_categories or []
        )
        if not grounded.items:
            return DomainAssessment(
                domain=ReviewDomain.COST_MODELING,
                vendor_id=vendor_id,
                status=DomainAssessmentStatus.FAILED,
                missing_information=grounded.missing if evidence else ["未提供成本证据"],
            )
        assessment = CostAssessment(
            **grounded.result(
                vendor_id=vendor_id,
                currency=currency,
                comparison_months=comparison_months,
            ),
            missing_costs=grounded.missing,
        )
        return DomainAssessment(
            domain=ReviewDomain.COST_MODELING,
            vendor_id=vendor_id,
            status=DomainAssessmentStatus.DEGRADED,
            cost_assessment=assessment,
            missing_information=grounded.missing,
            evidence_ids=assessment.evidence_ids,
        )


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
                if any(
                    marker.lower() in (item.context_text or item.quote).lower()
                    for marker in markers
                ):
                    grouped[category].append(item)

        findings: list[CommitmentFinding] = []
        conflicts: list[str] = []
        for category, items in grouped.items():
            normalized_quotes = list(
                dict.fromkeys((item.context_text or item.quote).strip() for item in items)
            )
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
