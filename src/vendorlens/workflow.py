from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from vendorlens.evidence import DeterministicEvidenceGate
from vendorlens.schemas import (
    Claim,
    EvidenceItem,
    FinalReport,
    ResearchPlan,
    ResearchSubtask,
    ReviewResult,
)


class PlannerAgent(Protocol):
    def plan(self, objective: str, project_context: str) -> ResearchPlan: ...


class RetrievalCoordinator(Protocol):
    def retrieve(self, plan: ResearchPlan, project_id: str) -> dict[str, list[EvidenceItem]]: ...


class AnalystAgent(Protocol):
    def analyze(
        self,
        subtask: ResearchSubtask,
        evidence: list[EvidenceItem],
        claims_to_revise: list[Claim],
        max_tool_rounds: int,
    ) -> list[Claim]: ...


class ReviewerAgent(Protocol):
    def review(
        self,
        claims: list[Claim],
        evidence: list[EvidenceItem],
        deterministic_result: ReviewResult,
    ) -> ReviewResult: ...


class WorkflowState(TypedDict, total=False):
    project_id: str
    objective: str
    project_context: str
    plan: ResearchPlan
    evidence_by_subtask: dict[str, list[EvidenceItem]]
    evidence: list[EvidenceItem]
    claims: list[Claim]
    review: ReviewResult
    revision_count: int
    pending_claim_ids: list[str]
    report: FinalReport
    status: str


class ReviewWorkflow:
    def __init__(
        self,
        planner: PlannerAgent,
        retriever: RetrievalCoordinator,
        analyst: AnalystAgent,
        reviewer: ReviewerAgent,
        evidence_gate: DeterministicEvidenceGate,
        checkpointer: BaseCheckpointSaver | None = None,
        max_parallel_subtasks: int = 4,
    ):
        self.planner = planner
        self.retriever = retriever
        self.analyst = analyst
        self.reviewer = reviewer
        self.evidence_gate = evidence_gate
        self.max_parallel_subtasks = max_parallel_subtasks
        self.graph = self._build().compile(checkpointer=checkpointer or InMemorySaver())

    def _build(self) -> StateGraph:
        graph = StateGraph(WorkflowState)
        graph.add_node("plan", self._plan)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("analyze", self._analyze)
        graph.add_node("review", self._review)
        graph.add_node("prepare_revision", self._prepare_revision)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "retrieve")
        graph.add_edge("retrieve", "analyze")
        graph.add_edge("analyze", "review")
        graph.add_conditional_edges(
            "review",
            self._route_review,
            {"revise": "prepare_revision", "finalize": "finalize"},
        )
        graph.add_edge("prepare_revision", "analyze")
        graph.add_edge("finalize", END)
        return graph

    def _plan(self, state: WorkflowState) -> dict:
        return {
            "plan": self.planner.plan(state["objective"], state.get("project_context", "")),
            "revision_count": 0,
            "status": "planned",
        }

    def _retrieve(self, state: WorkflowState) -> dict:
        evidence_by_subtask = self.retriever.retrieve(state["plan"], state["project_id"])
        evidence = [item for items in evidence_by_subtask.values() for item in items]
        unique = {item.id: item for item in evidence}
        return {
            "evidence_by_subtask": evidence_by_subtask,
            "evidence": list(unique.values()),
            "status": "retrieved",
        }

    def _analyze(self, state: WorkflowState) -> dict:
        pending = set(state.get("pending_claim_ids", []))
        existing = state.get("claims", [])
        claims_to_revise = [claim for claim in existing if claim.id in pending]
        retained = [claim for claim in existing if claim.id not in pending]
        subtasks = [
            task
            for task in state["plan"].subtasks
            if not pending or any(claim.subtask_id == task.id for claim in claims_to_revise)
        ]

        def run(task: ResearchSubtask) -> list[Claim]:
            relevant = [claim for claim in claims_to_revise if claim.subtask_id == task.id]
            return self.analyst.analyze(
                task,
                state["evidence_by_subtask"].get(task.id, []),
                relevant,
                max_tool_rounds=3,
            )

        produced: list[Claim] = []
        with ThreadPoolExecutor(max_workers=self.max_parallel_subtasks) as pool:
            futures = {pool.submit(run, task): task.id for task in subtasks}
            for future in as_completed(futures):
                produced.extend(future.result())
        return {"claims": retained + produced, "status": "analyzed"}

    def _review(self, state: WorkflowState) -> dict:
        deterministic = self.evidence_gate.review(state["claims"], state["evidence"])
        result = self.reviewer.review(state["claims"], state["evidence"], deterministic)
        return {"review": result, "status": "reviewed"}

    @staticmethod
    def _route_review(state: WorkflowState) -> str:
        if state["review"].passed or state.get("revision_count", 0) >= 2:
            return "finalize"
        return "revise"

    @staticmethod
    def _prepare_revision(state: WorkflowState) -> dict:
        return {
            "revision_count": state.get("revision_count", 0) + 1,
            "pending_claim_ids": state["review"].revision_claim_ids,
            "status": "revising",
        }

    @staticmethod
    def _finalize(state: WorkflowState) -> dict:
        review = state["review"]
        evidence = {item.id: item for item in state["evidence"]}
        matrix = [
            {
                "claim_id": claim.id,
                "statement": claim.statement,
                "evidence": [
                    evidence[item_id].model_dump()
                    for item_id in claim.evidence_ids
                    if item_id in evidence
                ],
            }
            for claim in state["claims"]
        ]
        report = FinalReport(
            executive_summary=(
                "评审已通过证据门禁。" if review.passed else "部分结论证据不足，需要人工处理。"
            ),
            comparison_matrix=[],
            risks=[],
            conflicts=review.conflicts,
            missing_information=review.missing_information,
            claim_evidence_matrix=matrix,
        )
        return {
            "report": report,
            "status": "completed" if review.passed else "partially_completed",
        }


def memory_workflow_factory(builder: Callable[[BaseCheckpointSaver], ReviewWorkflow]):
    return builder(InMemorySaver())
