import pytest
from pydantic import ValidationError

from vendorlens.review_agent import (
    ReviewOutputError,
    ReviewToolAccessError,
    VendorReviewAgent,
    VendorReviewDraft,
    VendorReviewExecutor,
    VendorReviewInput,
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


def review_input() -> VendorReviewInput:
    return VendorReviewInput(
        task_id="task-vendor-a",
        project_id="project-1",
        vendor_id="vendor-a",
        document_version_ids=["version-1"],
        evidence=[
            evidence("req-ev"),
            evidence("cost-ev", "实施费用 80 万元"),
            evidence("commit-ev", "合同签署后 90 天交付"),
        ],
        requirements=[RequirementItem(id="req-1", text="支持私有化部署", mandatory=True)],
        comparison_months=36,
        currency="CNY",
        required_commitments=["delivery"],
    )


def complete_draft(input_data, tools) -> VendorReviewDraft:
    return VendorReviewDraft(
        requirement_findings=[
            RequirementAssessment(
                vendor_id=input_data.vendor_id,
                requirement_id="req-1",
                mandatory=True,
                status=RequirementStatus.MET,
                explanation="供应商明确支持",
                evidence_ids=["req-ev"],
                confidence=0.95,
            )
        ],
        cost_assessment=CostAssessment(
            vendor_id=input_data.vendor_id,
            currency=input_data.currency,
            comparison_months=input_data.comparison_months,
            one_time_cost=800_000,
            recurring_cost=0,
            optional_cost=0,
            total_cost_of_ownership=800_000,
            evidence_ids=["cost-ev"],
        ),
        commitment_assessment=CommitmentAssessment(
            vendor_id=input_data.vendor_id,
            findings=[
                CommitmentFinding(
                    category="delivery",
                    statement="合同签署后 90 天交付",
                    status=CommitmentStatus.VERIFIED,
                    evidence_ids=["commit-ev"],
                )
            ],
        ),
    )


def executor(analyzer=complete_draft, *, tools=None) -> VendorReviewExecutor:
    return VendorReviewExecutor(VendorReviewAgent(analyzer), tools=tools)


def test_single_agent_returns_all_three_domain_results():
    result = executor().execute(review_input())

    assert set(result.domains) == set(ReviewDomain)
    assert all(item.status is DomainAssessmentStatus.COMPLETED for item in result.domains.values())
    assert result.domains[ReviewDomain.REQUIREMENT_COMPLIANCE].requirement_findings
    assert result.domains[ReviewDomain.COST_MODELING].cost_assessment is not None
    assert result.domains[ReviewDomain.COMMITMENT_VERIFICATION].commitment_assessment is not None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("project_id", "project-2", "frozen project"),
        ("document_version_id", "version-2", "frozen document versions"),
        ("vendor_id", "vendor-b", "another vendor"),
    ],
)
def test_review_input_rejects_evidence_outside_frozen_scope(field, value, message):
    foreign = evidence("foreign")
    setattr(foreign, field, value)
    input_data = review_input().model_dump()
    input_data["evidence"] = [foreign]

    with pytest.raises(ValidationError, match=message):
        VendorReviewInput.model_validate(input_data)


def test_single_agent_can_use_tools_from_multiple_business_sections():
    def analyzer(input_data, tools):
        requirement = tools.call("compare_requirement_response", "需求", "响应")
        tco = tools.call("calculate_tco", 800_000, 0, 36)
        draft = complete_draft(input_data, tools)
        draft.requirement_findings[0].explanation = requirement
        draft.cost_assessment.total_cost_of_ownership = tco
        return draft

    result = executor(
        analyzer,
        tools={
            "compare_requirement_response": lambda requirement, response: "明确满足",
            "calculate_tco": lambda one_time, annual, months: one_time + annual * months / 12,
        },
    ).execute(review_input())

    assert (
        result.domains[ReviewDomain.REQUIREMENT_COMPLIANCE].requirement_findings[0].explanation
        == "明确满足"
    )
    assert (
        result.domains[ReviewDomain.COST_MODELING].cost_assessment.total_cost_of_ownership
        == 800_000
    )


def test_unknown_tool_call_is_rejected():
    def invalid_analyzer(input_data, tools):
        tools.call("pick_winning_vendor")
        return complete_draft(input_data, tools)

    with pytest.raises(ReviewToolAccessError, match="pick_winning_vendor"):
        executor(invalid_analyzer).execute(review_input())


def test_executor_rejects_evidence_not_present_in_input_package():
    def hallucinating_analyzer(input_data, tools):
        draft = complete_draft(input_data, tools)
        draft.requirement_findings[0].evidence_ids = ["unknown-evidence"]
        return draft

    with pytest.raises(ReviewOutputError, match="unknown evidence"):
        executor(hallucinating_analyzer).execute(review_input())


def test_one_missing_section_fails_without_discarding_other_sections():
    def missing_cost_analyzer(input_data, tools):
        draft = complete_draft(input_data, tools)
        draft.cost_assessment = None
        draft.domain_errors[ReviewDomain.COST_MODELING] = "成本输出无法解析"
        return draft

    result = executor(missing_cost_analyzer).execute(review_input())

    assert result.domains[ReviewDomain.REQUIREMENT_COMPLIANCE].status is (
        DomainAssessmentStatus.COMPLETED
    )
    assert result.domains[ReviewDomain.COST_MODELING].status is DomainAssessmentStatus.FAILED
    assert result.domains[ReviewDomain.COST_MODELING].missing_information == ["成本输出无法解析"]
    assert result.domains[ReviewDomain.COMMITMENT_VERIFICATION].status is (
        DomainAssessmentStatus.COMPLETED
    )


@pytest.mark.parametrize("statement", [
    "合同签署后91天交付", "90个月交付", "90工作日交付", "90%可用性", "九十一天交付",
    "2026-10-02交付",
])
def test_numeric_hallucination_fails_only_commitment_domain(statement):
    def analyzer(input_data, tools):
        draft = complete_draft(input_data, tools)
        draft.commitment_assessment.findings[0].statement = statement
        return draft

    result = executor(analyzer).execute(review_input())
    assert result.domains[ReviewDomain.COMMITMENT_VERIFICATION].status is (
        DomainAssessmentStatus.FAILED
    )
    assert result.domains[ReviewDomain.COMMITMENT_VERIFICATION].commitment_assessment is None
    assert result.domains[ReviewDomain.REQUIREMENT_COMPLIANCE].status is (
        DomainAssessmentStatus.COMPLETED
    )
    assert result.domains[ReviewDomain.COST_MODELING].status is DomainAssessmentStatus.COMPLETED


def test_finding_cannot_borrow_a_number_from_another_findings_citation():
    def analyzer(input_data, tools):
        draft = complete_draft(input_data, tools)
        draft.requirement_findings[0].explanation = "支持90人并发"
        return draft

    result = executor(analyzer).execute(review_input())
    assert result.domains[ReviewDomain.REQUIREMENT_COMPLIANCE].status is (
        DomainAssessmentStatus.FAILED
    )


@pytest.mark.parametrize(("field", "value"), [
    ("one_time_cost", 880000), ("recurring_cost", 100000), ("optional_cost", 50000),
    ("total_cost_of_ownership", 1), ("payment_terms", ["首付30%"]),
    ("conflicts", ["另有1万美元费用"]),
])
def test_cost_fields_are_checked_even_for_a_custom_analyzer(field, value):
    def analyzer(input_data, tools):
        draft = complete_draft(input_data, tools)
        setattr(draft.cost_assessment, field, value)
        return draft

    result = executor(analyzer).execute(review_input())
    assert result.domains[ReviewDomain.COST_MODELING].status is DomainAssessmentStatus.FAILED
    assert result.domains[ReviewDomain.COST_MODELING].cost_assessment is None


def test_derived_tco_notes_are_authorized_only_with_an_explicit_derivation_label():
    def analyzer(input_data, tools):
        draft = complete_draft(input_data, tools)
        draft.cost_assessment.conflicts = ["比较三年的TCO为80万元"]
        return draft

    result = executor(analyzer).execute(review_input())
    assert result.domains[ReviewDomain.COST_MODELING].status is DomainAssessmentStatus.COMPLETED
    assert result.domains[ReviewDomain.COST_MODELING].cost_assessment.cost_sources


def test_parent_number_outside_child_quote_can_support_a_finding():
    from vendorlens.evidence import source_hash

    data = review_input().model_dump()
    parent = "合同签署后90天交付。质保24个月。"
    data["evidence"][2].update(context_text=parent, quote="合同签署后90天交付。",
                               source_text_hash=source_hash(parent))

    def analyzer(input_data, tools):
        draft = complete_draft(input_data, tools)
        draft.commitment_assessment.findings[0].statement = "父块说明质保两年"
        return draft

    result = executor(analyzer).execute(VendorReviewInput.model_validate(data))
    assert result.domains[ReviewDomain.COMMITMENT_VERIFICATION].status is (
        DomainAssessmentStatus.COMPLETED
    )


def test_domain_evidence_cannot_replace_canonical_content_with_same_id():
    data = review_input().model_dump()
    data["evidence_by_domain"] = {domain: list(data["evidence"]) for domain in ReviewDomain}
    forged = evidence("cost-ev", "实施费用90万元")
    data["evidence_by_domain"][ReviewDomain.COST_MODELING] = [forged]
    with pytest.raises(ValidationError, match="canonical"):
        VendorReviewInput.model_validate(data)


def test_duplicate_evidence_ids_are_rejected_before_model_execution():
    data = review_input().model_dump()
    data["evidence"].append(data["evidence"][0])
    with pytest.raises(ValidationError, match="unique"):
        VendorReviewInput.model_validate(data)


def test_known_but_cross_domain_citation_is_rejected_locally():
    data = review_input().model_dump()
    data["evidence_by_domain"] = {
        domain: [data["evidence"][index]] for index, domain in enumerate(ReviewDomain)
    }

    def analyzer(input_data, tools):
        draft = complete_draft(input_data, tools)
        draft.commitment_assessment.findings[0].evidence_ids = ["cost-ev"]
        return draft

    result = executor(analyzer).execute(VendorReviewInput.model_validate(data))
    failed = result.domains[ReviewDomain.COMMITMENT_VERIFICATION]
    assert failed.status is DomainAssessmentStatus.FAILED
    assert "domain_evidence_mismatch" in failed.missing_information
    assert result.domains[ReviewDomain.COST_MODELING].status is DomainAssessmentStatus.COMPLETED


def test_a_forged_cost_source_record_is_rebuilt_from_actual_citation():
    from vendorlens.schemas import CostSourceItem

    def analyzer(input_data, tools):
        draft = complete_draft(input_data, tools)
        draft.cost_assessment.cost_sources = [CostSourceItem(
            evidence_id="fake", source_text="伪造80万元", start=0, end=6,
            amount=800000, currency="CNY", period="one_time", category="one_time",
            annual_amount=800000,
        )]
        return draft

    result = executor(analyzer).execute(review_input())
    source = result.domains[ReviewDomain.COST_MODELING].cost_assessment.cost_sources[0]
    assert source.evidence_id == "cost-ev"
    assert source.source_text == "实施费用 80 万元"
