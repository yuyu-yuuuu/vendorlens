from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ClaimType(StrEnum):
    FACT = "fact"
    AMOUNT = "amount"
    DATE = "date"
    PERCENTAGE = "percentage"
    DURATION = "duration"
    RECOMMENDATION = "recommendation"


class PlanDimension(BaseModel):
    name: str
    weight: float = Field(ge=0, le=1)
    description: str


class ResearchSubtask(BaseModel):
    id: str
    title: str
    queries: list[str] = Field(min_length=1)
    dimension_names: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    objective: str
    dimensions: list[PlanDimension] = Field(min_length=1)
    subtasks: list[ResearchSubtask] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_weights_and_references(self) -> ResearchPlan:
        if abs(sum(item.weight for item in self.dimensions) - 1.0) > 1e-6:
            raise ValueError("dimension weights must sum to 1")
        dimensions = {item.name for item in self.dimensions}
        task_ids = {item.id for item in self.subtasks}
        if len(task_ids) != len(self.subtasks):
            raise ValueError("subtask ids must be unique")
        for task in self.subtasks:
            if not set(task.dimension_names) <= dimensions:
                raise ValueError(f"subtask {task.id} references an unknown dimension")
            if task.id in task.depends_on or not set(task.depends_on) <= task_ids:
                raise ValueError(f"subtask {task.id} has an invalid dependency")
        return self


class EvidenceItem(BaseModel):
    id: str
    project_id: str
    document_version_id: str
    chunk_id: str
    quote: str = Field(min_length=1)
    source_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    location: dict[str, Any]
    vendor_id: str | None = None


class Claim(BaseModel):
    id: str
    subtask_id: str
    statement: str = Field(min_length=1)
    claim_type: ClaimType
    evidence_ids: list[str] = Field(default_factory=list)
    structured_value: str | None = None
    unit: str | None = None
    confidence: float = Field(ge=0, le=1)

    @property
    def is_factual(self) -> bool:
        return self.claim_type is not ClaimType.RECOMMENDATION


class ClaimReview(BaseModel):
    claim_id: str
    passed: bool
    error_codes: list[str] = Field(default_factory=list)
    message: str


class ReviewResult(BaseModel):
    passed: bool
    claim_reviews: list[ClaimReview]
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    revision_claim_ids: list[str] = Field(default_factory=list)


class FinalReport(BaseModel):
    executive_summary: str
    comparison_matrix: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    missing_information: list[str]
    claim_evidence_matrix: list[dict[str, Any]]


class ReviewDomain(StrEnum):
    REQUIREMENT_COMPLIANCE = "requirement_compliance"
    COST_MODELING = "cost_modeling"
    COMMITMENT_VERIFICATION = "commitment_verification"


class RequirementStatus(StrEnum):
    MET = "met"
    PARTIALLY_MET = "partially_met"
    NOT_MET = "not_met"
    NOT_STATED = "not_stated"


class CommitmentStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    CONFLICTING = "conflicting"
    NOT_STATED = "not_stated"


class DomainAssessmentStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"


class RecommendationLevel(StrEnum):
    RECOMMENDED = "recommended"
    CONDITIONALLY_RECOMMENDED = "conditionally_recommended"
    UNABLE_TO_RECOMMEND = "unable_to_recommend"


class RequirementAssessment(BaseModel):
    vendor_id: str
    requirement_id: str
    mandatory: bool = False
    weight: float = Field(default=1.0, gt=0)
    status: RequirementStatus
    explanation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_evidence_for_stated_result(self) -> RequirementAssessment:
        if self.status is not RequirementStatus.NOT_STATED and not self.evidence_ids:
            raise ValueError("stated requirement assessment requires evidence")
        return self


class RequirementItem(BaseModel):
    id: str
    text: str = Field(min_length=1)
    mandatory: bool = False
    weight: float = Field(default=1.0, gt=0)


class CostAssessment(BaseModel):
    vendor_id: str
    currency: str = Field(min_length=3, max_length=3)
    comparison_months: int = Field(gt=0)
    one_time_cost: float = Field(ge=0)
    recurring_cost: float = Field(ge=0)
    optional_cost: float = Field(ge=0)
    total_cost_of_ownership: float = Field(ge=0)
    payment_terms: list[str] = Field(default_factory=list)
    missing_costs: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)

    @property
    def complete(self) -> bool:
        return not self.missing_costs and not self.conflicts


class CommitmentFinding(BaseModel):
    category: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    status: CommitmentStatus
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_evidence_for_stated_result(self) -> CommitmentFinding:
        if self.status is not CommitmentStatus.NOT_STATED and not self.evidence_ids:
            raise ValueError("stated commitment finding requires evidence")
        return self


class CommitmentAssessment(BaseModel):
    vendor_id: str
    findings: list[CommitmentFinding] = Field(min_length=1)
    missing_information: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)

    @property
    def complete(self) -> bool:
        return (
            not self.missing_information
            and not self.conflicts
            and all(item.status is CommitmentStatus.VERIFIED for item in self.findings)
        )


class DomainAssessment(BaseModel):
    domain: ReviewDomain
    vendor_id: str
    status: DomainAssessmentStatus
    requirement_findings: list[RequirementAssessment] = Field(default_factory=list)
    cost_assessment: CostAssessment | None = None
    commitment_assessment: CommitmentAssessment | None = None
    missing_information: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    revision_count: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def validate_domain_payload(self) -> DomainAssessment:
        payloads = {
            ReviewDomain.REQUIREMENT_COMPLIANCE: bool(self.requirement_findings),
            ReviewDomain.COST_MODELING: self.cost_assessment is not None,
            ReviewDomain.COMMITMENT_VERIFICATION: self.commitment_assessment is not None,
        }
        if (
            self.status
            in (
                DomainAssessmentStatus.COMPLETED,
                DomainAssessmentStatus.DEGRADED,
            )
            and not payloads[self.domain]
        ):
            raise ValueError("successful domain assessment requires its domain payload")
        return self


class VendorAssessment(BaseModel):
    vendor_id: str
    domains: dict[ReviewDomain, DomainAssessment]

    @model_validator(mode="after")
    def validate_domain_set_and_identity(self) -> VendorAssessment:
        if set(self.domains) != set(ReviewDomain):
            raise ValueError("vendor assessment requires exactly one result for every domain")
        for domain, assessment in self.domains.items():
            if assessment.domain is not domain:
                raise ValueError("domain assessment key does not match its payload")
            if assessment.vendor_id != self.vendor_id:
                raise ValueError("domain assessment belongs to another vendor")
        return self


class ReviewWeights(BaseModel):
    requirement_compliance: float = Field(default=0.5, ge=0, le=1)
    cost_competitiveness: float = Field(default=0.3, ge=0, le=1)
    commitment: float = Field(default=0.2, ge=0, le=1)

    @model_validator(mode="after")
    def validate_sum(self) -> ReviewWeights:
        if (
            abs(self.requirement_compliance + self.cost_competitiveness + self.commitment - 1.0)
            > 1e-9
        ):
            raise ValueError("weights must sum to 1")
        return self


class VendorRecommendationScore(BaseModel):
    vendor_id: str
    requirement_score: float = Field(ge=0, le=100)
    cost_score: float = Field(ge=0, le=100)
    commitment_score: float = Field(ge=0, le=100)
    total_score: float = Field(ge=0, le=100)
    eligible: bool
    disqualified: bool = False
    disqualification_reasons: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class RecommendationResult(BaseModel):
    level: RecommendationLevel
    recommended_vendor_ids: list[str] = Field(default_factory=list)
    vendor_scores: dict[str, VendorRecommendationScore]
    conditions: list[str] = Field(default_factory=list)
