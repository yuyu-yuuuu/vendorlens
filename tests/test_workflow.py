from vendorlens.coordinator import VendorEvidencePackage
from vendorlens.schemas import (
    CommitmentAssessment,
    CommitmentFinding,
    CommitmentStatus,
    CostAssessment,
    DomainAssessment,
    DomainAssessmentStatus,
    EvidenceItem,
    RecommendationLevel,
    RequirementAssessment,
    RequirementItem,
    RequirementStatus,
    ReviewDomain,
    VendorAssessment,
)
from vendorlens.workflow import DeterministicDomainFallbackRunner, ReviewWorkflow


def evidence(item_id: str, vendor_id: str, quote: str) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        project_id="project-1",
        vendor_id=vendor_id,
        document_version_id=f"version-{vendor_id}",
        chunk_id=f"chunk-{item_id}",
        quote=quote,
        source_text_hash="a" * 64,
        location={"page": 1},
    )


def package(vendor_id: str) -> VendorEvidencePackage:
    items = {
        ReviewDomain.REQUIREMENT_COMPLIANCE: [
            evidence(f"req-{vendor_id}", vendor_id, "支持私有化部署")
        ],
        ReviewDomain.COST_MODELING: [evidence(f"cost-{vendor_id}", vendor_id, "报价 80 万元")],
        ReviewDomain.COMMITMENT_VERIFICATION: [
            evidence(f"commit-{vendor_id}", vendor_id, "90 天交付")
        ],
    }
    return VendorEvidencePackage(
        project_id="project-1",
        vendor_id=vendor_id,
        document_version_ids=(f"version-{vendor_id}",),
        evidence_by_domain=items,
    )


def completed_assessment(
    vendor_id: str,
    *,
    failed_domain: ReviewDomain | None = None,
) -> VendorAssessment:
    requirement = DomainAssessment(
        domain=ReviewDomain.REQUIREMENT_COMPLIANCE,
        vendor_id=vendor_id,
        status=DomainAssessmentStatus.COMPLETED,
        requirement_findings=[
            RequirementAssessment(
                vendor_id=vendor_id,
                requirement_id="req-1",
                mandatory=True,
                status=RequirementStatus.MET,
                explanation="明确支持",
                evidence_ids=[f"req-{vendor_id}"],
                confidence=0.9,
            )
        ],
        evidence_ids=[f"req-{vendor_id}"],
    )
    cost = DomainAssessment(
        domain=ReviewDomain.COST_MODELING,
        vendor_id=vendor_id,
        status=DomainAssessmentStatus.COMPLETED,
        cost_assessment=CostAssessment(
            vendor_id=vendor_id,
            currency="CNY",
            comparison_months=36,
            one_time_cost=800_000,
            recurring_cost=0,
            optional_cost=0,
            total_cost_of_ownership=800_000,
            evidence_ids=[f"cost-{vendor_id}"],
        ),
        evidence_ids=[f"cost-{vendor_id}"],
    )
    commitment = DomainAssessment(
        domain=ReviewDomain.COMMITMENT_VERIFICATION,
        vendor_id=vendor_id,
        status=DomainAssessmentStatus.COMPLETED,
        commitment_assessment=CommitmentAssessment(
            vendor_id=vendor_id,
            findings=[
                CommitmentFinding(
                    category="delivery",
                    statement="90 天交付",
                    status=CommitmentStatus.VERIFIED,
                    evidence_ids=[f"commit-{vendor_id}"],
                )
            ],
        ),
        evidence_ids=[f"commit-{vendor_id}"],
    )
    domains = {
        ReviewDomain.REQUIREMENT_COMPLIANCE: requirement,
        ReviewDomain.COST_MODELING: cost,
        ReviewDomain.COMMITMENT_VERIFICATION: commitment,
    }
    if failed_domain is not None:
        domains[failed_domain] = DomainAssessment(
            domain=failed_domain,
            vendor_id=vendor_id,
            status=DomainAssessmentStatus.FAILED,
            missing_information=["测试失败"],
        )
    return VendorAssessment(vendor_id=vendor_id, domains=domains)


class Retriever:
    def __init__(self):
        self.calls = []

    def retrieve_vendor_evidence(self, *, project_id, vendor_id, queries_by_domain):
        self.calls.append(vendor_id)
        return package(vendor_id)


class PartialRetriever(Retriever):
    def retrieve_vendor_evidence(self, *, project_id, vendor_id, queries_by_domain):
        if vendor_id == "vendor-a":
            raise ValueError("甲供应商没有可用索引")
        return super().retrieve_vendor_evidence(
            project_id=project_id,
            vendor_id=vendor_id,
            queries_by_domain=queries_by_domain,
        )


class RetryRequirementExecutor:
    def __init__(self):
        self.calls = {}

    def execute(self, input_data):
        count = self.calls.get(input_data.vendor_id, 0) + 1
        self.calls[input_data.vendor_id] = count
        if input_data.vendor_id == "vendor-a" and count == 1:
            return completed_assessment(
                input_data.vendor_id,
                failed_domain=ReviewDomain.REQUIREMENT_COMPLIANCE,
            )
        return completed_assessment(input_data.vendor_id)


class AlwaysFailCostExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, input_data):
        self.calls += 1
        return completed_assessment(
            input_data.vendor_id,
            failed_domain=ReviewDomain.COST_MODELING,
        )


class CostFallback:
    def __init__(self):
        self.calls = []

    def run(self, input_data, domain):
        self.calls.append((input_data.vendor_id, domain))
        return DomainAssessment(
            domain=domain,
            vendor_id=input_data.vendor_id,
            status=DomainAssessmentStatus.DEGRADED,
            cost_assessment=CostAssessment(
                vendor_id=input_data.vendor_id,
                currency="CNY",
                comparison_months=36,
                one_time_cost=800_000,
                recurring_cost=0,
                optional_cost=0,
                total_cost_of_ownership=800_000,
                evidence_ids=[f"cost-{input_data.vendor_id}"],
            ),
            evidence_ids=[f"cost-{input_data.vendor_id}"],
        )


def initial_state(vendor_ids):
    return {
        "task_id": "task-1",
        "project_id": "project-1",
        "vendor_ids": vendor_ids,
        "requirements": [RequirementItem(id="req-1", text="支持私有化部署", mandatory=True)],
        "queries_by_domain": {
            ReviewDomain.REQUIREMENT_COMPLIANCE: ["私有化部署"],
            ReviewDomain.COST_MODELING: ["报价"],
            ReviewDomain.COMMITMENT_VERIFICATION: ["交付"],
        },
        "currency": "CNY",
        "comparison_months": 36,
        "required_commitments": ["delivery"],
    }


def test_graph_retries_only_vendor_with_failed_domain_and_checkpoints_state():
    retriever = Retriever()
    executor = RetryRequirementExecutor()
    workflow = ReviewWorkflow(retriever, executor, CostFallback())
    config = {"configurable": {"thread_id": "run-1"}}

    result = workflow.graph.invoke(initial_state(["vendor-a", "vendor-b"]), config)

    assert result["status"] == "completed"
    assert executor.calls == {"vendor-a": 2, "vendor-b": 1}
    assert result["revision_counts"]["vendor-a:requirement_compliance"] == 1
    assert result["recommendation"].level is RecommendationLevel.RECOMMENDED
    assert result["report"].comparison_matrix
    snapshot = workflow.graph.get_state(config)
    assert snapshot.values["status"] == "completed"


def test_graph_uses_domain_fallback_after_two_failed_revisions():
    executor = AlwaysFailCostExecutor()
    fallback = CostFallback()
    workflow = ReviewWorkflow(Retriever(), executor, fallback)

    result = workflow.graph.invoke(
        initial_state(["vendor-a"]),
        {"configurable": {"thread_id": "run-fallback"}},
    )

    cost = result["assessments"]["vendor-a"].domains[ReviewDomain.COST_MODELING]
    assert executor.calls == 3
    assert fallback.calls == [("vendor-a", ReviewDomain.COST_MODELING)]
    assert cost.status is DomainAssessmentStatus.DEGRADED
    assert result["recommendation"].level is RecommendationLevel.CONDITIONALLY_RECOMMENDED
    assert result["status"] == "partially_completed"


def test_retrieval_failure_for_one_vendor_preserves_other_vendor_result():
    executor = RetryRequirementExecutor()
    workflow = ReviewWorkflow(PartialRetriever(), executor, CostFallback())

    result = workflow.graph.invoke(
        initial_state(["vendor-a", "vendor-b"]),
        {"configurable": {"thread_id": "run-retrieval-partial"}},
    )

    assert executor.calls == {"vendor-b": 1}
    assert all(
        item.status is DomainAssessmentStatus.FAILED
        for item in result["assessments"]["vendor-a"].domains.values()
    )
    assert result["recommendation"].recommended_vendor_ids == ["vendor-b"]
    assert result["status"] == "partially_completed"


def test_default_cost_fallback_uses_only_cost_domain_evidence():
    input_data = package("vendor-a").to_review_input(
        task_id="task-a",
        requirements=[RequirementItem(id="req-1", text="支持私有化部署")],
        currency="CNY",
        comparison_months=36,
    )

    result = DeterministicDomainFallbackRunner().run(
        input_data,
        ReviewDomain.COST_MODELING,
    )

    assert result.status is DomainAssessmentStatus.DEGRADED
    assert result.cost_assessment is not None
    assert result.cost_assessment.total_cost_of_ownership == 800_000
    assert result.evidence_ids == ["cost-vendor-a"]
