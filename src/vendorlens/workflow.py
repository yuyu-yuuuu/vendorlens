from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from contextvars import copy_context
from typing import Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from vendorlens.coordinator import VendorEvidencePackage
from vendorlens.evidence import validate_numeric_output
from vendorlens.fallbacks import (
    CommitmentRuleChecker,
    DeterministicCostEngine,
    RequirementRuleEvaluator,
)
from vendorlens.recommendation import build_recommendation
from vendorlens.review_agent import VendorReviewInput
from vendorlens.schemas import (
    DomainAssessment,
    DomainAssessmentStatus,
    FinalReport,
    RecommendationLevel,
    RecommendationResult,
    RequirementItem,
    ReviewDomain,
    ReviewWeights,
    VendorAssessment,
)
from vendorlens.tracing import get_active_trace


class VendorEvidenceRetriever(Protocol):
    def retrieve_vendor_evidence(
        self,
        *,
        project_id: str,
        vendor_id: str,
        queries_by_domain: dict[ReviewDomain, list[str]],
    ) -> VendorEvidencePackage: ...


class VendorReviewRunner(Protocol):
    def execute(self, input_data: VendorReviewInput) -> VendorAssessment: ...


class DomainFallbackRunner(Protocol):
    def run(
        self,
        input_data: VendorReviewInput,
        domain: ReviewDomain,
    ) -> DomainAssessment: ...


class DeterministicDomainFallbackRunner:
    def __init__(self):
        self.requirements = RequirementRuleEvaluator()
        self.costs = DeterministicCostEngine()
        self.commitments = CommitmentRuleChecker()

    def run(
        self,
        input_data: VendorReviewInput,
        domain: ReviewDomain,
    ) -> DomainAssessment:
        evidence = input_data.evidence_by_domain.get(domain, input_data.evidence)
        if domain is ReviewDomain.REQUIREMENT_COMPLIANCE:
            evidence_by_requirement = {item.id: evidence for item in input_data.requirements}
            result = self.requirements.evaluate(
                input_data.vendor_id,
                input_data.requirements,
                evidence_by_requirement,
            )
        elif domain is ReviewDomain.COST_MODELING:
            result = self.costs.evaluate(
                input_data.vendor_id,
                evidence,
                currency=input_data.currency,
                comparison_months=input_data.comparison_months,
                required_cost_categories=input_data.required_cost_categories,
            )
        else:
            result = self.commitments.evaluate(input_data.vendor_id, evidence)
        return result.model_copy(update={"revision_count": input_data.revision_count})


class WorkflowState(TypedDict, total=False):
    task_id: str
    project_id: str
    vendor_ids: list[str]
    requirements: list[RequirementItem]
    queries_by_domain: dict[ReviewDomain, list[str]]
    currency: str
    comparison_months: int
    required_cost_categories: list[str]
    required_commitments: list[str]
    project_context: str
    memory_context: dict
    skill_snapshots: list[dict]
    weights: ReviewWeights
    review_inputs: dict[str, VendorReviewInput]
    retrieval_errors: dict[str, str]
    assessments: dict[str, VendorAssessment]
    pending_domains: dict[str, list[ReviewDomain]]
    revision_counts: dict[str, int]
    recommendation: RecommendationResult
    report: FinalReport
    status: str


class ReviewWorkflow:
    def __init__(
        self,
        retriever: VendorEvidenceRetriever,
        executor: VendorReviewRunner,
        fallback: DomainFallbackRunner | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        max_parallel_vendors: int = 4,
    ):
        self.retriever = retriever
        self.executor = executor
        self.fallback = fallback or DeterministicDomainFallbackRunner()
        self.max_parallel_vendors = max_parallel_vendors
        self.tracer = get_active_trace()
        self.graph = self._build().compile(checkpointer=checkpointer or InMemorySaver())

    def _build(self) -> StateGraph:
        graph = StateGraph(WorkflowState)
        for name, handler in {
            "prepare_review": self._prepare_review,
            "retrieve_vendor_evidence": self._retrieve_vendor_evidence,
            "analyze_vendors": self._analyze_vendors,
            "validate_domains": self._validate_domains,
            "prepare_domain_revisions": self._prepare_domain_revisions,
            "run_domain_fallbacks": self._run_domain_fallbacks,
            "aggregate_scores": self._aggregate_scores,
            "compose_report": self._compose_report,
        }.items():
            graph.add_node(name, self._trace_node(name, handler))
        graph.add_edge(START, "prepare_review")
        graph.add_edge("prepare_review", "retrieve_vendor_evidence")
        graph.add_edge("retrieve_vendor_evidence", "analyze_vendors")
        graph.add_edge("analyze_vendors", "validate_domains")
        graph.add_conditional_edges(
            "validate_domains",
            self._route_after_validation,
            {
                "revise": "prepare_domain_revisions",
                "fallback": "run_domain_fallbacks",
                "aggregate": "aggregate_scores",
            },
        )
        graph.add_edge("prepare_domain_revisions", "analyze_vendors")
        graph.add_edge("run_domain_fallbacks", "aggregate_scores")
        graph.add_edge("aggregate_scores", "compose_report")
        graph.add_edge("compose_report", END)
        return graph

    def _trace_node(self, name, handler):
        if self.tracer is None:
            return handler

        def traced(state: WorkflowState) -> dict:
            with self.tracer.span(name, "node") as span:
                result = handler(state)
                span.attributes["state_status"] = result.get("status", "")
                return result

        return traced

    @staticmethod
    def _prepare_review(state: WorkflowState) -> dict:
        vendor_ids = state.get("vendor_ids", [])
        if not vendor_ids or len(set(vendor_ids)) != len(vendor_ids):
            raise ValueError("review requires unique vendor ids")
        if not state.get("requirements"):
            raise ValueError("review requires a requirement catalog")
        if set(state.get("queries_by_domain", {})) != set(ReviewDomain):
            raise ValueError("review requires queries for every domain")
        pending = {vendor_id: list(ReviewDomain) for vendor_id in vendor_ids}
        counts = {
            _domain_key(vendor_id, domain): 0 for vendor_id in vendor_ids for domain in ReviewDomain
        }
        return {
            "pending_domains": pending,
            "revision_counts": counts,
            "assessments": {},
            "weights": ReviewWeights.model_validate(state.get("weights", ReviewWeights())),
            "status": "prepared",
        }

    def _retrieve_vendor_evidence(self, state: WorkflowState) -> dict:
        def retrieve(vendor_id: str) -> VendorReviewInput:
            scope = (
                self.tracer.span("retrieve_vendor", "retrieval", vendor_id=vendor_id)
                if self.tracer
                else nullcontext()
            )
            with scope as span:
                result = retrieve_input(vendor_id)
                if span:
                    span.attributes.update(
                        evidence_count=len(result.evidence),
                        version_count=len(result.document_version_ids),
                    )
                return result

        def retrieve_input(vendor_id: str) -> VendorReviewInput:
            package = self.retriever.retrieve_vendor_evidence(
                project_id=state["project_id"],
                vendor_id=vendor_id,
                queries_by_domain=state["queries_by_domain"],
            )
            input_data = package.to_review_input(
                task_id=state["task_id"],
                requirements=state["requirements"],
                currency=state["currency"],
                comparison_months=state["comparison_months"],
                required_cost_categories=state.get("required_cost_categories", []),
                required_commitments=state.get("required_commitments", []),
                project_context=state.get("project_context", ""),
            )
            from vendorlens.conversation_memory import vendor_memory

            return input_data.model_copy(update={
                "memory_context": vendor_memory(
                    state.get("memory_context", {}), vendor_id, input_data.document_version_ids
                ),
                "skill_snapshots": state.get("skill_snapshots", []),
            })

        inputs: dict[str, VendorReviewInput] = {}
        errors: dict[str, str] = {}
        counts = dict(state["revision_counts"])
        with ThreadPoolExecutor(max_workers=self.max_parallel_vendors) as pool:
            futures = {
                pool.submit(copy_context().run, retrieve, vendor_id): vendor_id
                for vendor_id in state["vendor_ids"]
            }
            for future in as_completed(futures):
                vendor_id = futures[future]
                try:
                    inputs[vendor_id] = future.result()
                except Exception as error:
                    errors[vendor_id] = str(error) or "供应商证据检索失败"
                    for domain in ReviewDomain:
                        counts[_domain_key(vendor_id, domain)] = 2
        return {
            "review_inputs": inputs,
            "retrieval_errors": errors,
            "revision_counts": counts,
            "status": "retrieved",
        }

    def _analyze_vendors(self, state: WorkflowState) -> dict:
        current = dict(state.get("assessments", {}))
        targets = {
            vendor_id: domains
            for vendor_id, domains in state.get("pending_domains", {}).items()
            if domains
        }

        def analyze(vendor_id: str, domains: list[ReviewDomain]) -> VendorAssessment:
            if vendor_id not in state["review_inputs"]:
                return _failed_vendor_assessment(
                    vendor_id,
                    state.get("retrieval_errors", {}).get(vendor_id, "供应商证据输入不可用"),
                )
            counts = [
                state["revision_counts"][_domain_key(vendor_id, domain)] for domain in domains
            ]
            input_data = state["review_inputs"][vendor_id].model_copy(
                update={
                    "revision_domains": set(domains),
                    "revision_count": max(counts, default=0),
                    "revision_feedback": {
                        domain: current[vendor_id].domains[domain].missing_information
                        for domain in domains if vendor_id in current
                    },
                }
            )
            try:
                scope = (
                    self.tracer.span(
                        "VendorReviewAgent",
                        "agent",
                        vendor_id=vendor_id,
                        revision_count=input_data.revision_count,
                        domain_count=len(domains),
                    )
                    if self.tracer
                    else nullcontext()
                )
                with scope as span:
                    result = self.executor.execute(input_data)
                    if span:
                        failed = sum(
                            result.domains[d].status is DomainAssessmentStatus.FAILED
                            for d in domains
                        )
                        span.attributes["failed_domain_count"] = failed
                        span.status = (
                            "failed"
                            if failed == len(domains)
                            else "partially_completed"
                            if failed
                            else "completed"
                        )
                    return result
            except Exception as error:
                return _failed_vendor_assessment(vendor_id, str(error))

        produced: dict[str, VendorAssessment] = {}
        with ThreadPoolExecutor(max_workers=self.max_parallel_vendors) as pool:
            futures = {
                pool.submit(copy_context().run, analyze, vendor_id, domains): (vendor_id, domains)
                for vendor_id, domains in targets.items()
            }
            for future in as_completed(futures):
                vendor_id, domains = futures[future]
                result = future.result()
                previous = current.get(vendor_id)
                merged = dict(previous.domains) if previous is not None else dict(result.domains)
                for domain in domains:
                    count = state["revision_counts"][_domain_key(vendor_id, domain)]
                    merged[domain] = result.domains[domain].model_copy(
                        update={"revision_count": count}
                    )
                produced[vendor_id] = VendorAssessment(vendor_id=vendor_id, domains=merged)
        current.update(produced)
        return {"assessments": current, "status": "analyzed"}

    @staticmethod
    def _validate_domains(state: WorkflowState) -> dict:
        assessments = {
            vendor_id: validate_numeric_output(state["review_inputs"][vendor_id], result)
            if vendor_id in state["review_inputs"] else result
            for vendor_id, result in state["assessments"].items()
        }
        pending = {
            vendor_id: [
                domain
                for domain, assessment in result.domains.items()
                if assessment.status is DomainAssessmentStatus.FAILED
            ]
            for vendor_id, result in assessments.items()
        }
        return {"assessments": assessments, "pending_domains": pending, "status": "validated"}

    @staticmethod
    def _route_after_validation(state: WorkflowState) -> str:
        pending = [
            (vendor_id, domain)
            for vendor_id, domains in state.get("pending_domains", {}).items()
            for domain in domains
        ]
        if not pending:
            return "aggregate"
        if any(
            state["revision_counts"][_domain_key(vendor_id, domain)] < 2
            for vendor_id, domain in pending
        ):
            return "revise"
        return "fallback"

    @staticmethod
    def _prepare_domain_revisions(state: WorkflowState) -> dict:
        counts = dict(state["revision_counts"])
        retryable: dict[str, list[ReviewDomain]] = {}
        for vendor_id, domains in state["pending_domains"].items():
            for domain in domains:
                key = _domain_key(vendor_id, domain)
                if counts[key] >= 2:
                    continue
                counts[key] += 1
                retryable.setdefault(vendor_id, []).append(domain)
        return {
            "revision_counts": counts,
            "pending_domains": retryable,
            "status": "revising",
        }

    def _run_domain_fallbacks(self, state: WorkflowState) -> dict:
        assessments = dict(state["assessments"])
        for vendor_id, domains in state["pending_domains"].items():
            merged = dict(assessments[vendor_id].domains)
            for domain in domains:
                count = state["revision_counts"][_domain_key(vendor_id, domain)]
                if vendor_id not in state["review_inputs"]:
                    merged[domain] = _failed_domain(
                        vendor_id,
                        domain,
                        state.get("retrieval_errors", {}).get(vendor_id, "供应商证据输入不可用"),
                        count,
                    )
                    continue
                input_data = state["review_inputs"][vendor_id].model_copy(
                    update={
                        "revision_domains": {domain},
                        "revision_count": count,
                    }
                )
                try:
                    scope = (
                        self.tracer.span(
                            "domain_fallback",
                            "fallback",
                            vendor_id=vendor_id,
                            domain=domain.value,
                            revision_count=count,
                        )
                        if self.tracer
                        else nullcontext()
                    )
                    with scope as span:
                        result = self.fallback.run(input_data, domain)
                        if result.domain is not domain or result.vendor_id != vendor_id:
                            raise ValueError("fallback output identity does not match its input")
                        if result.status not in (
                            DomainAssessmentStatus.DEGRADED,
                            DomainAssessmentStatus.FAILED,
                        ):
                            raise ValueError("fallback must return degraded or failed status")
                        candidate = dict(merged)
                        candidate[domain] = result
                        result = validate_numeric_output(
                            input_data,
                            VendorAssessment(vendor_id=vendor_id, domains=candidate),
                        ).domains[domain]
                        if span:
                            span.status = result.status.value
                except Exception as error:
                    result = _failed_domain(vendor_id, domain, str(error), count)
                merged[domain] = result.model_copy(update={"revision_count": count})
            assessments[vendor_id] = VendorAssessment(
                vendor_id=vendor_id,
                domains=merged,
            )
        return {"assessments": assessments, "status": "degraded"}

    @staticmethod
    def _aggregate_scores(state: WorkflowState) -> dict:
        requirements = []
        costs = []
        commitments = []
        degraded_vendor_ids: set[str] = set()
        for vendor_id, result in state["assessments"].items():
            for assessment in result.domains.values():
                if assessment.status is DomainAssessmentStatus.DEGRADED:
                    degraded_vendor_ids.add(vendor_id)
                requirements.extend(assessment.requirement_findings)
                if assessment.cost_assessment is not None:
                    costs.append(assessment.cost_assessment)
                if assessment.commitment_assessment is not None:
                    commitments.append(assessment.commitment_assessment)
        recommendation = build_recommendation(
            requirements=requirements,
            costs=costs,
            commitments=commitments,
            weights=state["weights"],
            degraded_vendor_ids=degraded_vendor_ids,
        )
        return {"recommendation": recommendation, "status": "scored"}

    @staticmethod
    def _compose_report(state: WorkflowState) -> dict:
        recommendation = state["recommendation"]
        assessments = state["assessments"]
        scores = [
            recommendation.vendor_scores[vendor_id].model_dump()
            for vendor_id in sorted(recommendation.vendor_scores)
        ]
        risks = [
            {
                "vendor_id": vendor_id,
                "disqualification_reasons": score.disqualification_reasons,
                "conditions": score.conditions,
            }
            for vendor_id, score in sorted(recommendation.vendor_scores.items())
            if score.disqualification_reasons or score.conditions
        ]
        conflicts = [
            {"vendor_id": vendor_id, "domain": domain.value, "detail": conflict}
            for vendor_id, result in assessments.items()
            for domain, assessment in result.domains.items()
            for conflict in assessment.conflicts
        ]
        missing = [
            f"{vendor_id}/{domain.value}: {detail}"
            for vendor_id, result in assessments.items()
            for domain, assessment in result.domains.items()
            for detail in assessment.missing_information
        ]
        matrix = [
            {
                "vendor_id": vendor_id,
                "domain": domain.value,
                "status": assessment.status.value,
                "evidence_ids": assessment.evidence_ids,
            }
            for vendor_id, result in assessments.items()
            for domain, assessment in result.domains.items()
        ]
        report = FinalReport(
            executive_summary=_recommendation_summary(recommendation),
            comparison_matrix=scores,
            risks=risks,
            conflicts=conflicts,
            missing_information=missing,
            claim_evidence_matrix=matrix,
        )
        statuses = [
            assessment.status
            for result in assessments.values()
            for assessment in result.domains.values()
        ]
        if statuses and all(item is DomainAssessmentStatus.FAILED for item in statuses):
            status = "failed"
        elif any(
            item in (DomainAssessmentStatus.DEGRADED, DomainAssessmentStatus.FAILED)
            for item in statuses
        ):
            status = "partially_completed"
        else:
            status = "completed"
        return {"report": report, "status": status}


def _domain_key(vendor_id: str, domain: ReviewDomain) -> str:
    return f"{vendor_id}:{domain.value}"


def _failed_domain(
    vendor_id: str,
    domain: ReviewDomain,
    reason: str,
    revision_count: int = 0,
) -> DomainAssessment:
    return DomainAssessment(
        domain=domain,
        vendor_id=vendor_id,
        status=DomainAssessmentStatus.FAILED,
        missing_information=[reason or "领域分析失败"],
        revision_count=revision_count,
    )


def _failed_vendor_assessment(vendor_id: str, reason: str) -> VendorAssessment:
    return VendorAssessment(
        vendor_id=vendor_id,
        domains={domain: _failed_domain(vendor_id, domain, reason) for domain in ReviewDomain},
    )


def _recommendation_summary(recommendation: RecommendationResult) -> str:
    if recommendation.level is RecommendationLevel.UNABLE_TO_RECOMMEND:
        return "现有证据不足以形成供应商推荐。"
    vendors = "、".join(recommendation.recommended_vendor_ids)
    if recommendation.level is RecommendationLevel.CONDITIONALLY_RECOMMENDED:
        return f"有条件推荐：{vendors}。"
    return f"推荐：{vendors}。"


def memory_workflow_factory(builder: Callable[[BaseCheckpointSaver], ReviewWorkflow]):
    return builder(InMemorySaver())
