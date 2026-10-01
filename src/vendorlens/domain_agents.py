from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
)

DOMAIN_TOOL_ALLOWLISTS = {
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


class DomainAgentInputBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    vendor_id: str = Field(min_length=1)
    document_version_ids: list[str] = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    project_context: str = ""
    revision_targets: list[str] = Field(default_factory=list)
    revision_count: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def validate_frozen_evidence_scope(self) -> DomainAgentInputBase:
        version_ids = set(self.document_version_ids)
        if len(version_ids) != len(self.document_version_ids):
            raise ValueError("document version ids must be unique")
        for item in self.evidence:
            if item.project_id != self.project_id:
                raise ValueError("evidence is outside the frozen project")
            if item.document_version_id not in version_ids:
                raise ValueError("evidence is outside the frozen document versions")
        return self


class RequirementAgentInput(DomainAgentInputBase):
    domain: Literal[ReviewDomain.REQUIREMENT_COMPLIANCE] = ReviewDomain.REQUIREMENT_COMPLIANCE
    requirements: list[RequirementItem] = Field(min_length=1)


class CostAgentInput(DomainAgentInputBase):
    domain: Literal[ReviewDomain.COST_MODELING] = ReviewDomain.COST_MODELING
    currency: str = Field(min_length=3, max_length=3)
    comparison_months: int = Field(gt=0)
    required_cost_categories: list[str] = Field(default_factory=list)


class CommitmentAgentInput(DomainAgentInputBase):
    domain: Literal[ReviewDomain.COMMITMENT_VERIFICATION] = ReviewDomain.COMMITMENT_VERIFICATION
    required_commitments: list[str] = Field(default_factory=list)


type DomainAgentInput = RequirementAgentInput | CostAgentInput | CommitmentAgentInput
type ToolFunction = Callable[..., Any]


class DomainAgentError(RuntimeError):
    pass


class DomainRoutingError(DomainAgentError):
    pass


class DomainToolAccessError(DomainAgentError):
    pass


class DomainToolUnavailableError(DomainAgentError):
    pass


class DomainOutputError(DomainAgentError):
    pass


class DomainAgentExecutionError(DomainAgentError):
    pass


class DomainToolbox:
    def __init__(
        self,
        domain: ReviewDomain,
        tools: Mapping[str, ToolFunction],
    ):
        self.domain = domain
        self.allowed_names = DOMAIN_TOOL_ALLOWLISTS[domain]
        self._tools = tools

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in self.allowed_names:
            raise DomainToolAccessError(f"tool {name!r} is not allowed for domain {self.domain}")
        if name not in self._tools:
            raise DomainToolUnavailableError(f"allowed tool {name!r} is not registered")
        return self._tools[name](*args, **kwargs)


class DomainAgent(Protocol):
    domain: ReviewDomain

    def analyze(
        self,
        input_data: DomainAgentInput,
        tools: DomainToolbox,
    ) -> DomainAssessment: ...


type RequirementAnalyzer = Callable[
    [RequirementAgentInput, DomainToolbox], list[RequirementAssessment]
]
type CostAnalyzer = Callable[[CostAgentInput, DomainToolbox], CostAssessment]
type CommitmentAnalyzer = Callable[[CommitmentAgentInput, DomainToolbox], CommitmentAssessment]


class RequirementComplianceAgent:
    domain = ReviewDomain.REQUIREMENT_COMPLIANCE

    def __init__(self, analyzer: RequirementAnalyzer):
        self._analyzer = analyzer

    def analyze(
        self,
        input_data: DomainAgentInput,
        tools: DomainToolbox,
    ) -> DomainAssessment:
        if not isinstance(input_data, RequirementAgentInput):
            raise DomainRoutingError("requirement agent received the wrong input contract")
        findings = self._analyzer(input_data, tools)
        return DomainAssessment(
            domain=self.domain,
            vendor_id=input_data.vendor_id,
            status=DomainAssessmentStatus.COMPLETED,
            requirement_findings=findings,
            missing_information=[
                item.requirement_id
                for item in findings
                if item.status is RequirementStatus.NOT_STATED
            ],
            evidence_ids=_unique(
                evidence_id for item in findings for evidence_id in item.evidence_ids
            ),
            revision_count=input_data.revision_count,
        )


class CostModelingAgent:
    domain = ReviewDomain.COST_MODELING

    def __init__(self, analyzer: CostAnalyzer):
        self._analyzer = analyzer

    def analyze(
        self,
        input_data: DomainAgentInput,
        tools: DomainToolbox,
    ) -> DomainAssessment:
        if not isinstance(input_data, CostAgentInput):
            raise DomainRoutingError("cost agent received the wrong input contract")
        assessment = self._analyzer(input_data, tools)
        return DomainAssessment(
            domain=self.domain,
            vendor_id=input_data.vendor_id,
            status=DomainAssessmentStatus.COMPLETED,
            cost_assessment=assessment,
            missing_information=assessment.missing_costs,
            conflicts=assessment.conflicts,
            evidence_ids=assessment.evidence_ids,
            revision_count=input_data.revision_count,
        )


class CommitmentVerificationAgent:
    domain = ReviewDomain.COMMITMENT_VERIFICATION

    def __init__(self, analyzer: CommitmentAnalyzer):
        self._analyzer = analyzer

    def analyze(
        self,
        input_data: DomainAgentInput,
        tools: DomainToolbox,
    ) -> DomainAssessment:
        if not isinstance(input_data, CommitmentAgentInput):
            raise DomainRoutingError("commitment agent received the wrong input contract")
        assessment = self._analyzer(input_data, tools)
        return DomainAssessment(
            domain=self.domain,
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


INPUT_TYPES: dict[ReviewDomain, type[DomainAgentInputBase]] = {
    ReviewDomain.REQUIREMENT_COMPLIANCE: RequirementAgentInput,
    ReviewDomain.COST_MODELING: CostAgentInput,
    ReviewDomain.COMMITMENT_VERIFICATION: CommitmentAgentInput,
}


class DomainAgentRouter:
    def __init__(self, agents: Sequence[DomainAgent]):
        self._agents: dict[ReviewDomain, DomainAgent] = {}
        for agent in agents:
            if agent.domain in self._agents:
                raise ValueError(f"duplicate agent for domain {agent.domain}")
            self._agents[agent.domain] = agent
        missing = set(ReviewDomain) - set(self._agents)
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"missing agents for domains: {names}")

    def route(self, input_data: DomainAgentInput) -> DomainAgent:
        expected_type = INPUT_TYPES[input_data.domain]
        if not isinstance(input_data, expected_type):
            raise DomainRoutingError(
                f"domain {input_data.domain} requires input type {expected_type.__name__}"
            )
        return self._agents[input_data.domain]


class DomainAgentExecutor:
    def __init__(
        self,
        router: DomainAgentRouter,
        tools: Mapping[str, ToolFunction] | None = None,
    ):
        self.router = router
        self.tools = tools or {}

    def execute(self, input_data: DomainAgentInput) -> DomainAssessment:
        agent = self.router.route(input_data)
        toolbox = DomainToolbox(input_data.domain, self.tools)
        try:
            result = agent.analyze(input_data, toolbox)
        except DomainAgentError:
            raise
        except Exception as error:
            raise DomainAgentExecutionError(
                f"domain agent execution failed for task {input_data.task_id}"
            ) from error
        self._validate_output(input_data, result)
        return result

    @staticmethod
    def _validate_output(
        input_data: DomainAgentInput,
        result: DomainAssessment,
    ) -> None:
        if result.domain is not input_data.domain:
            raise DomainOutputError("agent output domain does not match its input")
        if result.vendor_id != input_data.vendor_id:
            raise DomainOutputError("agent output vendor does not match its input")
        if result.status is not DomainAssessmentStatus.COMPLETED:
            raise DomainOutputError("domain agent must return a completed assessment")
        if result.revision_count != input_data.revision_count:
            raise DomainOutputError("agent output revision count does not match its input")

        available_evidence_ids = {item.id for item in input_data.evidence}
        unknown_evidence_ids = _referenced_evidence_ids(result) - available_evidence_ids
        if unknown_evidence_ids:
            unknown = ", ".join(sorted(unknown_evidence_ids))
            raise DomainOutputError(f"agent output references unknown evidence: {unknown}")

        _validate_payload_identity(input_data, result)


def _validate_payload_identity(
    input_data: DomainAgentInput,
    result: DomainAssessment,
) -> None:
    if isinstance(input_data, RequirementAgentInput):
        requirement_ids = {item.id for item in input_data.requirements}
        for finding in result.requirement_findings:
            if finding.vendor_id != input_data.vendor_id:
                raise DomainOutputError("requirement finding belongs to another vendor")
            if finding.requirement_id not in requirement_ids:
                raise DomainOutputError("requirement finding is outside the input catalog")
    elif isinstance(input_data, CostAgentInput):
        assessment = result.cost_assessment
        if assessment is None or assessment.vendor_id != input_data.vendor_id:
            raise DomainOutputError("cost assessment belongs to another vendor")
        if assessment.currency != input_data.currency:
            raise DomainOutputError("cost assessment currency does not match its input")
        if assessment.comparison_months != input_data.comparison_months:
            raise DomainOutputError("cost assessment period does not match its input")
    else:
        assessment = result.commitment_assessment
        if assessment is None or assessment.vendor_id != input_data.vendor_id:
            raise DomainOutputError("commitment assessment belongs to another vendor")


def _referenced_evidence_ids(result: DomainAssessment) -> set[str]:
    evidence_ids = set(result.evidence_ids)
    evidence_ids.update(
        evidence_id
        for finding in result.requirement_findings
        for evidence_id in finding.evidence_ids
    )
    if result.cost_assessment is not None:
        evidence_ids.update(result.cost_assessment.evidence_ids)
    if result.commitment_assessment is not None:
        evidence_ids.update(
            evidence_id
            for finding in result.commitment_assessment.findings
            for evidence_id in finding.evidence_ids
        )
    return evidence_ids


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
