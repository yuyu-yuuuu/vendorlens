import pytest

from vendorlens.review_agent import ReviewToolAccessError, VendorReviewInput
from vendorlens.review_tools import ReviewToolRegistry
from vendorlens.schemas import EvidenceItem, RequirementItem, ReviewDomain


def item(item_id, quote, domain_vendor="vendor-a"):
    return EvidenceItem(
        id=item_id,
        project_id="project-1",
        document_version_id="version-1",
        chunk_id=f"chunk-{item_id}",
        quote=quote,
        source_text_hash="a" * 64,
        location={"page": 1},
        vendor_id=domain_vendor,
    )


def review_input():
    requirement = item("req-ev", "供应商明确支持私有化部署")
    cost = item("cost-ev", "实施费用 80 万元，年度维保费 10 万元")
    commitment = item("commit-ev", "合同签署后 90 天交付")
    return VendorReviewInput(
        task_id="task-1",
        project_id="project-1",
        vendor_id="vendor-a",
        document_version_ids=["version-1"],
        evidence=[requirement, cost, commitment],
        evidence_by_domain={
            ReviewDomain.REQUIREMENT_COMPLIANCE: [requirement],
            ReviewDomain.COST_MODELING: [cost],
            ReviewDomain.COMMITMENT_VERIFICATION: [commitment],
        },
        requirements=[RequirementItem(id="req-1", text="支持私有化部署", mandatory=True)],
        currency="CNY",
        comparison_months=36,
        required_commitments=["delivery"],
    )


def test_registry_rejects_cross_domain_evidence():
    registry = ReviewToolRegistry(review_input())

    with pytest.raises(ReviewToolAccessError, match="cost_modeling"):
        registry.call("extract_cost_items", evidence_ids=["req-ev"])


def test_registry_calculates_authoritative_tco():
    registry = ReviewToolRegistry(review_input())

    result = registry.call(
        "calculate_tco",
        one_time_cost=800_000,
        annual_recurring_cost=100_000,
        optional_cost=50_000,
        evidence_ids=["cost-ev"],
    )

    assert result["total_cost_of_ownership"] == 1_100_000
    assert result["optional_cost"] == 50_000
    assert registry.authoritative_cost == result


def test_registry_exposes_only_declared_json_schema_tools():
    registry = ReviewToolRegistry(review_input())

    definitions = registry.definitions()

    assert {tool["function"]["name"] for tool in definitions} == registry.names
    assert all(tool["type"] == "function" for tool in definitions)


def test_requirement_tools_search_compare_and_mark_missing():
    registry = ReviewToolRegistry(review_input())

    matches = registry.call(
        "search_requirement_evidence",
        requirement_id="req-1",
        query="私有化",
    )
    comparison = registry.call(
        "compare_requirement_response",
        requirement_id="req-1",
        evidence_ids=["req-ev"],
    )
    missing = registry.call("mark_missing_response", requirement_id="req-1")

    assert matches[0]["evidence_id"] == "req-ev"
    assert comparison["status"] == "met"
    assert missing == {"status": "not_stated", "evidence_ids": []}


def test_cost_tools_extract_normalize_and_compare():
    registry = ReviewToolRegistry(review_input())

    extracted = registry.call("extract_cost_items", evidence_ids=["cost-ev"])
    normalized = registry.call("normalize_cost", amount=10_000, period="monthly")
    compared = registry.call("compare_cost_items", items=[extracted[0], extracted[0]])

    assert [item["amount"] for item in extracted] == [800_000, 100_000]
    assert normalized["annual_amount"] == 120_000
    assert compared["duplicate_count"] == 1


def test_commitment_tools_use_only_commitment_evidence():
    registry = ReviewToolRegistry(review_input())

    extracted = registry.call("extract_commitments", evidence_ids=["commit-ev"])
    milestones = registry.call("compare_milestones", evidence_ids=["commit-ev"])
    dependencies = registry.call("check_dependencies", evidence_ids=["commit-ev"])
    conflicts = registry.call("detect_commitment_conflicts", evidence_ids=["commit-ev"])

    assert extracted[0]["evidence_id"] == "commit-ev"
    assert milestones == {"values": ["90"], "conflicting": False}
    assert dependencies == {"dependencies": []}
    assert conflicts == milestones


def test_registry_rejects_unknown_tool_and_invalid_arguments():
    registry = ReviewToolRegistry(review_input())

    with pytest.raises(ReviewToolAccessError, match="not registered"):
        registry.call("pick_winner")
    with pytest.raises(ReviewToolAccessError, match="invalid arguments"):
        registry.call("normalize_cost", amount=-1, period="annual")
