from vendorlens.evidence import DeterministicEvidenceGate, SourceRecord, source_hash
from vendorlens.schemas import (
    Claim,
    ClaimType,
    EvidenceItem,
    PlanDimension,
    ResearchPlan,
    ResearchSubtask,
)
from vendorlens.workflow import ReviewWorkflow

TEXT = "甲供应商含税总报价为 88 万元。"


class Repository:
    def get_source(self, chunk_id):
        if chunk_id != "chunk-1":
            return None
        return SourceRecord("p1", "v1", "chunk-1", TEXT, source_hash(TEXT))


class Planner:
    def plan(self, objective, project_context):
        return ResearchPlan(
            objective=objective,
            dimensions=[PlanDimension(name="价格", weight=1.0, description="总价")],
            subtasks=[
                ResearchSubtask(
                    id="price", title="比较报价", queries=["供应商报价"], dimension_names=["价格"]
                )
            ],
        )


class Retriever:
    def retrieve(self, plan, project_id):
        return {
            "price": [
                EvidenceItem(
                    id="ev-1",
                    project_id=project_id,
                    document_version_id="v1",
                    chunk_id="chunk-1",
                    quote="含税总报价为 88 万元",
                    source_text_hash=source_hash(TEXT),
                    location={"page": 2},
                )
            ]
        }


class Analyst:
    def __init__(self):
        self.calls = 0

    def analyze(self, subtask, evidence, claims_to_revise, max_tool_rounds):
        self.calls += 1
        value = "890000" if self.calls == 1 else "880000"
        return [
            Claim(
                id="claim-price",
                subtask_id=subtask.id,
                statement=f"甲供应商报价为 {value} 元。",
                claim_type=ClaimType.AMOUNT,
                structured_value=value,
                unit="CNY",
                confidence=0.9,
                evidence_ids=["ev-1"],
            )
        ]


class Reviewer:
    def review(self, claims, evidence, deterministic_result):
        return deterministic_result


def test_graph_revises_only_failed_claim_and_checkpoints_state():
    analyst = Analyst()
    workflow = ReviewWorkflow(
        Planner(),
        Retriever(),
        analyst,
        Reviewer(),
        DeterministicEvidenceGate(Repository()),
    )
    config = {"configurable": {"thread_id": "run-1"}}

    result = workflow.graph.invoke(
        {"project_id": "p1", "objective": "选择供应商", "project_context": ""}, config
    )

    assert result["status"] == "completed"
    assert result["revision_count"] == 1
    assert analyst.calls == 2
    assert result["report"].claim_evidence_matrix[0]["claim_id"] == "claim-price"
    snapshot = workflow.graph.get_state(config)
    assert snapshot.values["status"] == "completed"


def test_graph_stops_after_two_revisions_and_marks_partial():
    class AlwaysWrong(Analyst):
        def analyze(self, subtask, evidence, claims_to_revise, max_tool_rounds):
            self.calls += 1
            return [
                Claim(
                    id="claim-price",
                    subtask_id=subtask.id,
                    statement="报价为 99 万元",
                    claim_type=ClaimType.AMOUNT,
                    structured_value="990000",
                    confidence=0.8,
                    evidence_ids=["ev-1"],
                )
            ]

    analyst = AlwaysWrong()
    workflow = ReviewWorkflow(
        Planner(),
        Retriever(),
        analyst,
        Reviewer(),
        DeterministicEvidenceGate(Repository()),
    )
    result = workflow.graph.invoke(
        {"project_id": "p1", "objective": "选择供应商"},
        {"configurable": {"thread_id": "run-partial"}},
    )

    assert result["status"] == "partially_completed"
    assert result["revision_count"] == 2
    assert analyst.calls == 3
    assert result["report"].missing_information
