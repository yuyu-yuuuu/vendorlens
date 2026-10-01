import pytest
from pydantic import ValidationError

from vendorlens.domain_agents import (
    DOMAIN_TOOL_ALLOWLISTS,
    CommitmentAgentInput,
    CommitmentVerificationAgent,
    CostAgentInput,
    CostModelingAgent,
    DomainAgentExecutor,
    DomainAgentRouter,
    DomainOutputError,
    DomainToolAccessError,
    RequirementAgentInput,
    RequirementComplianceAgent,
)
from vendorlens.schemas import (
    CommitmentAssessment,
    CommitmentFinding,
    CommitmentStatus,
    CostAssessment,
    DomainAssessmentStatus,
    EvidenceItem,
    RequirementAssessment,
    RequirementItem,
    RequirementStatus,
    ReviewDomain,
)


def evidence(item_id: str, quote: str = "供应商明确支持私有化部署") -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        project_id="project-1",
        document_version_id="version-1",
        chunk_id=f"chunk-{item_id}",
        quote=quote,
        source_text_hash="a" * 64,
        location={"page": 1},
    )


def requirement_input() -> RequirementAgentInput:
    return RequirementAgentInput(
        task_id="task-requirement-a",
        project_id="project-1",
        vendor_id="vendor-a",
        document_version_ids=["version-1"],
        evidence=[evidence("ev-1")],
        requirements=[RequirementItem(id="req-1", text="支持私有化部署", mandatory=True)],
    )


def cost_input() -> CostAgentInput:
    return CostAgentInput(
        task_id="task-cost-a",
        project_id="project-1",
        vendor_id="vendor-a",
        document_version_ids=["version-1"],
        evidence=[evidence("cost-1", "实施费用 80 万元")],
        comparison_months=36,
        currency="CNY",
    )


def commitment_input() -> CommitmentAgentInput:
    return CommitmentAgentInput(
        task_id="task-commitment-a",
        project_id="project-1",
        vendor_id="vendor-a",
        document_version_ids=["version-1"],
        evidence=[evidence("commit-1", "合同签署后 90 天交付")],
        required_commitments=["delivery"],
    )


def requirement_analyzer(input_data, tools):
    return [
        RequirementAssessment(
            vendor_id=input_data.vendor_id,
            requirement_id="req-1",
            mandatory=True,
            status=RequirementStatus.MET,
            explanation="供应商明确支持",
            evidence_ids=["ev-1"],
            confidence=0.95,
        )
    ]


def cost_analyzer(input_data, tools):
    return CostAssessment(
        vendor_id=input_data.vendor_id,
        currency=input_data.currency,
        comparison_months=input_data.comparison_months,
        one_time_cost=800_000,
        recurring_cost=0,
        optional_cost=0,
        total_cost_of_ownership=800_000,
        evidence_ids=["cost-1"],
    )


def commitment_analyzer(input_data, tools):
    return CommitmentAssessment(
        vendor_id=input_data.vendor_id,
        findings=[
            CommitmentFinding(
                category="delivery",
                statement="合同签署后 90 天交付",
                status=CommitmentStatus.VERIFIED,
                evidence_ids=["commit-1"],
            )
        ],
    )


def executor(*, requirement=requirement_analyzer) -> DomainAgentExecutor:
    router = DomainAgentRouter(
        [
            RequirementComplianceAgent(requirement),
            CostModelingAgent(cost_analyzer),
            CommitmentVerificationAgent(commitment_analyzer),
        ]
    )
    return DomainAgentExecutor(router)


def test_domain_tool_allowlists_do_not_overlap():
    tool_sets = list(DOMAIN_TOOL_ALLOWLISTS.values())

    assert len(tool_sets) == 3
    assert not tool_sets[0] & tool_sets[1]
    assert not tool_sets[0] & tool_sets[2]
    assert not tool_sets[1] & tool_sets[2]


def test_router_selects_agent_deterministically_from_domain():
    runner = executor()

    requirement_result = runner.execute(requirement_input())
    cost_result = runner.execute(cost_input())
    commitment_result = runner.execute(commitment_input())

    assert requirement_result.domain is ReviewDomain.REQUIREMENT_COMPLIANCE
    assert cost_result.domain is ReviewDomain.COST_MODELING
    assert commitment_result.domain is ReviewDomain.COMMITMENT_VERIFICATION
    assert all(
        item.status is DomainAssessmentStatus.COMPLETED
        for item in (requirement_result, cost_result, commitment_result)
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("project_id", "project-2", "frozen project"),
        ("document_version_id", "version-2", "frozen document versions"),
    ],
)
def test_domain_input_rejects_evidence_outside_frozen_scope(field, value, message):
    foreign = evidence("foreign")
    setattr(foreign, field, value)

    with pytest.raises(ValidationError, match=message):
        RequirementAgentInput(
            task_id="task-requirement-a",
            project_id="project-1",
            vendor_id="vendor-a",
            document_version_ids=["version-1"],
            evidence=[foreign],
            requirements=[RequirementItem(id="req-1", text="支持私有化部署")],
        )


def test_cross_domain_tool_call_is_rejected():
    def invalid_requirement_analyzer(input_data, tools):
        tools.call("calculate_tco", [])
        return requirement_analyzer(input_data, tools)

    with pytest.raises(DomainToolAccessError, match="calculate_tco"):
        executor(requirement=invalid_requirement_analyzer).execute(requirement_input())


def test_executor_rejects_evidence_not_present_in_input_package():
    def hallucinating_analyzer(input_data, tools):
        finding = requirement_analyzer(input_data, tools)[0]
        return [finding.model_copy(update={"evidence_ids": ["unknown-evidence"]})]

    with pytest.raises(DomainOutputError, match="unknown evidence"):
        executor(requirement=hallucinating_analyzer).execute(requirement_input())


def test_router_requires_one_agent_for_every_domain():
    with pytest.raises(ValueError, match="missing agents"):
        DomainAgentRouter([RequirementComplianceAgent(requirement_analyzer)])
