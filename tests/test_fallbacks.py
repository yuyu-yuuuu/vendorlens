from vendorlens.fallbacks import (
    DOMAIN_TOOL_ALLOWLISTS,
    CommitmentRuleChecker,
    DeterministicCostEngine,
    RequirementRuleEvaluator,
)
from vendorlens.schemas import (
    CommitmentStatus,
    DomainAssessmentStatus,
    EvidenceItem,
    RequirementItem,
    RequirementStatus,
)


def evidence(item_id: str, quote: str) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        project_id="project-1",
        document_version_id="version-1",
        chunk_id=f"chunk-{item_id}",
        quote=quote,
        source_text_hash="a" * 64,
        location={"page": 1},
    )


def test_domain_tool_allowlists_do_not_overlap():
    tool_sets = list(DOMAIN_TOOL_ALLOWLISTS.values())

    assert len(tool_sets) == 3
    assert not tool_sets[0] & tool_sets[1]
    assert not tool_sets[0] & tool_sets[2]
    assert not tool_sets[1] & tool_sets[2]


def test_requirement_fallback_uses_only_explicit_response():
    requirements = [
        RequirementItem(id="req-1", text="系统支持私有化部署", mandatory=True),
        RequirementItem(id="req-2", text="支持多语言界面"),
        RequirementItem(id="req-3", text="支持离线运行"),
    ]

    result = RequirementRuleEvaluator().evaluate(
        "vendor-a",
        requirements,
        {
            "req-1": [evidence("ev-1", "本系统支持私有化部署。")],
            "req-2": [evidence("ev-2", "当前版本不支持多语言界面。")],
        },
    )

    assert result.status is DomainAssessmentStatus.DEGRADED
    assert [item.status for item in result.requirement_findings] == [
        RequirementStatus.MET,
        RequirementStatus.NOT_MET,
        RequirementStatus.NOT_STATED,
    ]


def test_cost_fallback_extracts_amounts_and_calculates_three_year_tco():
    result = DeterministicCostEngine().evaluate(
        "vendor-a",
        [
            evidence("cost-1", "软件及实施一次性报价 80 万元。"),
            evidence("cost-2", "每年维保费用 10 万元。"),
            evidence("cost-3", "可选培训服务 5 万元。"),
        ],
        comparison_months=36,
    )

    cost = result.cost_assessment
    assert result.status is DomainAssessmentStatus.DEGRADED
    assert cost is not None
    assert cost.one_time_cost == 800_000
    assert cost.recurring_cost == 100_000
    assert cost.optional_cost == 50_000
    assert cost.total_cost_of_ownership == 1_100_000


def test_commitment_conflict_is_preserved_in_degraded_result():
    result = CommitmentRuleChecker().evaluate(
        "vendor-a",
        [
            evidence("commit-1", "项目将在合同签署后 90 天交付。"),
            evidence("commit-2", "整体交付周期为 120 天。"),
        ],
    )

    assert result.status is DomainAssessmentStatus.DEGRADED
    assert result.commitment_assessment is not None
    assert result.commitment_assessment.findings[0].status is CommitmentStatus.CONFLICTING
    assert result.conflicts == [
        "delivery: 项目将在合同签署后 90 天交付。 | 整体交付周期为 120 天。"
    ]
    assert set(result.evidence_ids) == {"commit-1", "commit-2"}


def test_clear_commitment_remains_a_degraded_result_without_conflicts():
    result = CommitmentRuleChecker().evaluate(
        "vendor-a", [evidence("commit-1", "项目将在合同签署后 90 天交付。")]
    )

    assert result.status is DomainAssessmentStatus.DEGRADED
    assert result.conflicts == []
    assert result.commitment_assessment is not None
    assert result.commitment_assessment.findings[0].status is CommitmentStatus.VERIFIED


def test_fallback_without_required_input_fails_cleanly():
    requirement_result = RequirementRuleEvaluator().evaluate("vendor-a", [], {})
    cost_result = DeterministicCostEngine().evaluate("vendor-a", [])

    assert requirement_result.status is DomainAssessmentStatus.FAILED
    assert requirement_result.missing_information == ["未提供需求目录"]
    assert cost_result.status is DomainAssessmentStatus.FAILED
    assert cost_result.missing_information == ["未提供成本证据"]
