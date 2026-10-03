from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vendorlens.model_adapter import ModelError
from vendorlens.schemas import (
    CommitmentAssessment,
    CostAssessment,
    DomainAssessment,
    DomainAssessmentStatus,
    EvidenceItem,
    RequirementAssessment,
    RequirementItem,
    RequirementStatus,
    ReviewDomain,
    VendorAssessment,
)

REVIEW_TOOLS_BY_DOMAIN = {
    ReviewDomain.REQUIREMENT_COMPLIANCE: frozenset(
        {
            "search_requirement_evidence",
            "compare_requirement_response",
            "mark_missing_response",
        }
    ),
    ReviewDomain.COST_MODELING: frozenset(
        {"extract_cost_items", "normalize_cost", "calculate_tco", "compare_cost_items"}
    ),
    ReviewDomain.COMMITMENT_VERIFICATION: frozenset(
        {
            "extract_commitments",
            "compare_milestones",
            "check_dependencies",
            "detect_commitment_conflicts",
        }
    ),
}
KNOWN_REVIEW_TOOLS = frozenset().union(*REVIEW_TOOLS_BY_DOMAIN.values())


class VendorReviewInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    vendor_id: str = Field(min_length=1)
    document_version_ids: list[str] = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_by_domain: dict[ReviewDomain, list[EvidenceItem]] = Field(default_factory=dict)
    requirements: list[RequirementItem] = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    comparison_months: int = Field(gt=0)
    required_cost_categories: list[str] = Field(default_factory=list)
    required_commitments: list[str] = Field(default_factory=list)
    project_context: str = ""
    memory_context: dict[str, Any] = Field(default_factory=dict)
    skill_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    revision_domains: set[ReviewDomain] = Field(default_factory=set)
    revision_count: int = Field(default=0, ge=0, le=2)
    revision_feedback: dict[ReviewDomain, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_frozen_evidence_scope(self) -> VendorReviewInput:
        from vendorlens.skills import validate_skill_snapshots

        validate_skill_snapshots(self.skill_snapshots)
        version_ids = set(self.document_version_ids)
        if len(version_ids) != len(self.document_version_ids):
            raise ValueError("document version ids must be unique")
        canonical = {item.id: item for item in self.evidence}
        if len(canonical) != len(self.evidence):
            raise ValueError("evidence ids must be unique")
        for item in self.evidence:
            if item.project_id != self.project_id:
                raise ValueError("evidence is outside the frozen project")
            if item.document_version_id not in version_ids:
                raise ValueError("evidence is outside the frozen document versions")
            if item.vendor_id not in (None, self.vendor_id):
                raise ValueError("evidence belongs to another vendor")
        if self.evidence_by_domain:
            if set(self.evidence_by_domain) != set(ReviewDomain):
                raise ValueError("domain evidence must cover every review domain")
            grouped_ids = {item.id for items in self.evidence_by_domain.values() for item in items}
            if grouped_ids - set(canonical):
                raise ValueError("domain evidence must be included in the review evidence")
            for items in self.evidence_by_domain.values():
                for item in items:
                    if item != canonical[item.id]:
                        raise ValueError("domain evidence must match the canonical evidence")
        return self


class VendorReviewDraft(BaseModel):
    requirement_findings: list[RequirementAssessment] = Field(default_factory=list)
    cost_assessment: CostAssessment | None = None
    commitment_assessment: CommitmentAssessment | None = None
    domain_errors: dict[ReviewDomain, str] = Field(default_factory=dict)


type ReviewTool = Callable[..., Any]
type ReviewAnalyzer = Callable[[VendorReviewInput, ReviewToolbox], VendorReviewDraft]


class ReviewAgentError(RuntimeError):
    pass


class ReviewToolAccessError(ReviewAgentError):
    pass


class ReviewToolGroundingError(ReviewToolAccessError):
    """A registered cost tool's inputs are unsupported by its cited source."""


class ReviewToolUnavailableError(ReviewAgentError):
    pass


class ReviewOutputError(ReviewAgentError):
    pass


class ReviewAgentExecutionError(ReviewAgentError):
    pass


class ReviewToolbox:
    def __init__(self, tools: Mapping[str, ReviewTool]):
        self.allowed_names = KNOWN_REVIEW_TOOLS
        self._tools = tools

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in self.allowed_names:
            raise ReviewToolAccessError(f"tool {name!r} is not a VendorReviewAgent tool")
        if name not in self._tools:
            raise ReviewToolUnavailableError(f"review tool {name!r} is not registered")
        return self._tools[name](*args, **kwargs)


class ReviewAgent(Protocol):
    def analyze(
        self,
        input_data: VendorReviewInput,
        tools: ReviewToolbox,
    ) -> VendorAssessment: ...


class VendorReviewAgent:
    def __init__(self, analyzer: ReviewAnalyzer):
        self._analyzer = analyzer

    def analyze(
        self,
        input_data: VendorReviewInput,
        tools: ReviewToolbox,
    ) -> VendorAssessment:
        draft = VendorReviewDraft.model_validate(self._analyzer(input_data, tools))
        domains = {
            ReviewDomain.REQUIREMENT_COMPLIANCE: self._requirement_result(input_data, draft),
            ReviewDomain.COST_MODELING: self._cost_result(input_data, draft),
            ReviewDomain.COMMITMENT_VERIFICATION: self._commitment_result(input_data, draft),
        }
        return VendorAssessment(vendor_id=input_data.vendor_id, domains=domains)

    @staticmethod
    def _requirement_result(
        input_data: VendorReviewInput,
        draft: VendorReviewDraft,
    ) -> DomainAssessment:
        if not draft.requirement_findings:
            return _failed_domain(
                input_data,
                ReviewDomain.REQUIREMENT_COMPLIANCE,
                draft.domain_errors.get(
                    ReviewDomain.REQUIREMENT_COMPLIANCE,
                    "需求符合性输出缺失",
                ),
            )
        return DomainAssessment(
            domain=ReviewDomain.REQUIREMENT_COMPLIANCE,
            vendor_id=input_data.vendor_id,
            status=DomainAssessmentStatus.COMPLETED,
            requirement_findings=draft.requirement_findings,
            missing_information=[
                item.requirement_id
                for item in draft.requirement_findings
                if item.status is RequirementStatus.NOT_STATED
            ],
            evidence_ids=_unique(
                evidence_id
                for item in draft.requirement_findings
                for evidence_id in item.evidence_ids
            ),
            revision_count=input_data.revision_count,
        )

    @staticmethod
    def _cost_result(
        input_data: VendorReviewInput,
        draft: VendorReviewDraft,
    ) -> DomainAssessment:
        if draft.cost_assessment is None:
            return _failed_domain(
                input_data,
                ReviewDomain.COST_MODELING,
                draft.domain_errors.get(ReviewDomain.COST_MODELING, "成本输出缺失"),
            )
        return DomainAssessment(
            domain=ReviewDomain.COST_MODELING,
            vendor_id=input_data.vendor_id,
            status=DomainAssessmentStatus.COMPLETED,
            cost_assessment=draft.cost_assessment,
            missing_information=draft.cost_assessment.missing_costs,
            conflicts=draft.cost_assessment.conflicts,
            evidence_ids=draft.cost_assessment.evidence_ids,
            revision_count=input_data.revision_count,
        )

    @staticmethod
    def _commitment_result(
        input_data: VendorReviewInput,
        draft: VendorReviewDraft,
    ) -> DomainAssessment:
        if draft.commitment_assessment is None:
            return _failed_domain(
                input_data,
                ReviewDomain.COMMITMENT_VERIFICATION,
                draft.domain_errors.get(
                    ReviewDomain.COMMITMENT_VERIFICATION,
                    "承诺核验输出缺失",
                ),
            )
        assessment = draft.commitment_assessment
        return DomainAssessment(
            domain=ReviewDomain.COMMITMENT_VERIFICATION,
            vendor_id=input_data.vendor_id,
            status=DomainAssessmentStatus.COMPLETED,
            commitment_assessment=assessment,
            missing_information=assessment.missing_information,
            conflicts=assessment.conflicts,
            evidence_ids=_unique(
                evidence_id
                for finding in assessment.findings
                for evidence_id in finding.evidence_ids
            ),
            revision_count=input_data.revision_count,
        )


class VendorReviewExecutor:
    def __init__(
        self,
        agent: ReviewAgent,
        tools: Mapping[str, ReviewTool] | None = None,
    ):
        self.agent = agent
        self.tools = tools or {}

    def execute(self, input_data: VendorReviewInput) -> VendorAssessment:
        # Revalidate nested mutable evidence even for model_copy-created retry inputs.
        input_data = VendorReviewInput.model_validate(input_data.model_dump())
        try:
            result = self.agent.analyze(input_data, ReviewToolbox(self.tools))
        except (ReviewAgentError, ModelError):
            raise
        except Exception as error:
            raise ReviewAgentExecutionError(
                f"vendor review execution failed for task {input_data.task_id}"
            ) from error
        self._validate_output(input_data, result)
        from vendorlens.evidence import validate_numeric_output

        return validate_numeric_output(input_data, result)

    @staticmethod
    def _validate_output(
        input_data: VendorReviewInput,
        result: VendorAssessment,
    ) -> None:
        if result.vendor_id != input_data.vendor_id:
            raise ReviewOutputError("review output vendor does not match its input")
        for assessment in result.domains.values():
            if assessment.revision_count != input_data.revision_count:
                raise ReviewOutputError("review output revision count does not match its input")

        available_evidence_ids = {item.id for item in input_data.evidence}
        unknown_evidence_ids = _referenced_evidence_ids(result) - available_evidence_ids
        if unknown_evidence_ids:
            unknown = ", ".join(sorted(unknown_evidence_ids))
            raise ReviewOutputError(f"review output references unknown evidence: {unknown}")

        _validate_payload_identity(input_data, result)


def _failed_domain(
    input_data: VendorReviewInput,
    domain: ReviewDomain,
    reason: str,
) -> DomainAssessment:
    return DomainAssessment(
        domain=domain,
        vendor_id=input_data.vendor_id,
        status=DomainAssessmentStatus.FAILED,
        missing_information=[reason],
        revision_count=input_data.revision_count,
    )


def _validate_payload_identity(
    input_data: VendorReviewInput,
    result: VendorAssessment,
) -> None:
    requirement_result = result.domains[ReviewDomain.REQUIREMENT_COMPLIANCE]
    requirement_ids = {item.id for item in input_data.requirements}
    for finding in requirement_result.requirement_findings:
        if finding.vendor_id != input_data.vendor_id:
            raise ReviewOutputError("requirement finding belongs to another vendor")
        if finding.requirement_id not in requirement_ids:
            raise ReviewOutputError("requirement finding is outside the input catalog")

    cost = result.domains[ReviewDomain.COST_MODELING].cost_assessment
    if cost is not None:
        if cost.vendor_id != input_data.vendor_id:
            raise ReviewOutputError("cost assessment belongs to another vendor")
        if cost.currency != input_data.currency:
            raise ReviewOutputError("cost assessment currency does not match its input")
        if cost.comparison_months != input_data.comparison_months:
            raise ReviewOutputError("cost assessment period does not match its input")

    commitment = result.domains[ReviewDomain.COMMITMENT_VERIFICATION].commitment_assessment
    if commitment is not None and commitment.vendor_id != input_data.vendor_id:
        raise ReviewOutputError("commitment assessment belongs to another vendor")


def _referenced_evidence_ids(result: VendorAssessment) -> set[str]:
    evidence_ids: set[str] = set()
    for assessment in result.domains.values():
        evidence_ids.update(assessment.evidence_ids)
        evidence_ids.update(
            evidence_id
            for finding in assessment.requirement_findings
            for evidence_id in finding.evidence_ids
        )
        if assessment.cost_assessment is not None:
            evidence_ids.update(assessment.cost_assessment.evidence_ids)
        if assessment.commitment_assessment is not None:
            evidence_ids.update(
                evidence_id
                for finding in assessment.commitment_assessment.findings
                for evidence_id in finding.evidence_ids
            )
    return evidence_ids


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
