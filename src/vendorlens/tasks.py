from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import timedelta
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from vendorlens.config import get_settings
from vendorlens.events import RunEventStore
from vendorlens.models import (
    AnalysisReport,
    DomainAssessmentRecord,
    Project,
    Requirement,
    ResearchTask,
    ReviewConfig,
    TraceSpan,
    Vendor,
    new_id,
    utcnow,
)
from vendorlens.recovery import LeaseHeartbeat, TaskLeaseLost, own_task
from vendorlens.schemas import RequirementItem, ReviewDomain, ReviewWeights
from vendorlens.tracing import TraceRecorder
from vendorlens.workflow import ReviewWorkflow

TERMINAL_STATUSES = {"completed", "partially_completed", "failed", "enqueue_failed"}


class ReviewTaskRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "objective": "比较供应商方案与三年总拥有成本",
                    "vendor_ids": ["55555555-5555-4555-8555-555555555555"],
                }
            ]
        },
    )
    objective: str = Field(min_length=1, max_length=4000, description="评审目标，不能仅包含空白")
    vendor_ids: list[str] = Field(
        min_length=1, max_length=20, description="同一项目内 1-20 个供应商 ID，不能重复"
    )
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    user_id: str = Field(default="local", min_length=1, max_length=100)
    industry: str = Field(default="general", min_length=1, max_length=80)
    comparison_months: int | None = Field(default=None, gt=0, le=1200)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    queries_by_domain: dict[ReviewDomain, list[str]] | None = Field(
        default=None,
        description="可省略以使用默认查询；提供时必须覆盖三个领域，"
        "每个领域 1-20 条查询，每条非空且最多 2000 字",
        examples=[
            {
                "requirement_compliance": ["是否支持私有化部署"],
                "cost_modeling": ["软件、实施和年度维护费用"],
                "commitment_verification": ["交付周期和客户配合条件"],
            }
        ],
    )

    @model_validator(mode="after")
    def validate_request(self):
        if not self.objective.strip():
            raise ValueError("objective must not be blank")
        if len(set(self.vendor_ids)) != len(self.vendor_ids):
            raise ValueError("vendor ids must be unique")
        if self.queries_by_domain is not None:
            if set(self.queries_by_domain) != set(ReviewDomain):
                raise ValueError("queries must cover all review domains")
            if any(
                not queries
                or len(queries) > 20
                or any(not q.strip() or len(q) > 2000 for q in queries)
                for queries in self.queries_by_domain.values()
            ):
                raise ValueError(
                    "each domain requires 1-20 nonblank queries of at most 2000 characters"
                )
        return self


def create_review_task(
    session: Session, project_id: str, request: ReviewTaskRequest
) -> ResearchTask:
    project = session.get(Project, project_id)
    if project is None:
        raise LookupError("project not found")
    vendors = set(session.scalars(select(Vendor.id).where(Vendor.project_id == project_id)))
    if not set(request.vendor_ids) <= vendors:
        raise ValueError("vendors must belong to the project")
    requirements = list(
        session.scalars(
            select(Requirement)
            .where(Requirement.project_id == project_id)
            .order_by(Requirement.code)
        )
    )
    if not requirements:
        raise ValueError("project requires a requirement catalog before review")
    config = session.scalar(select(ReviewConfig).where(ReviewConfig.project_id == project_id))
    from vendorlens.conversation_memory import ConversationMemory
    from vendorlens.skills import skill_manager

    memory = ConversationMemory()
    memory_context = {}
    conversation = None
    if request.session_id:
        conversation = memory.scoped_session(
            session, request.session_id, project_id, request.user_id
        )
        memory_context = memory.context(
            session, conversation, request.objective, request.vendor_ids
        )
    preferences = memory_context.get("preferences", {})
    skills = skill_manager(get_settings().skills_root).select(request.industry, request.objective)
    weights = (
        ReviewWeights()
        if config is None
        else ReviewWeights(
            requirement_compliance=config.requirement_weight,
            cost_competitiveness=config.cost_weight,
            commitment=config.commitment_weight,
        )
    )
    queries = request.queries_by_domain or {
        ReviewDomain.REQUIREMENT_COMPLIANCE: [r.text for r in requirements],
        ReviewDomain.COST_MODELING: ["软件 实施 维护 报价 付款条件"],
        ReviewDomain.COMMITMENT_VERIFICATION: ["交付 上线 服务承诺 客户配合条件"],
    }
    snapshot = {
        "project_id": project_id,
        "vendor_ids": request.vendor_ids,
        "requirements": [
            RequirementItem(
                id=r.id, text=r.text, mandatory=r.mandatory, weight=r.weight
            ).model_dump(mode="json")
            for r in requirements
        ],
        "queries_by_domain": {domain.value: values for domain, values in queries.items()},
        "currency": request.currency or (config.currency if config else (
            preferences.get("currency", "CNY"))),
        "comparison_months": request.comparison_months or (config.comparison_months if config else (
            preferences.get("comparison_months", 36))),
        "required_cost_categories": config.required_cost_categories
        if config
        else ["software", "implementation", "maintenance"],
        "required_commitments": config.commitment_checklist if config else [],
        "project_context": f"{project.description}\n评审目标：{request.objective}",
        "weights": weights.model_dump(mode="json"),
        "memory_context": memory_context,
        "skill_snapshots": skills,
        "industry": request.industry,
        "conversation": {"session_id": conversation.id, "user_id": conversation.user_id}
        if conversation else None,
    }
    if snapshot["comparison_months"] <= 0:
        raise ValueError("comparison months must be positive")
    task = ResearchTask(
        project_id=project_id, objective=request.objective, request_snapshot=snapshot
    )
    session.add(task)
    session.flush()
    if conversation:
        memory.append(session, conversation, "user", request.objective, task_id=task.id)
    RunEventStore().append(session, task.id, "run.queued", {"run_id": task.run_id})
    from vendorlens.outbox import schedule

    schedule(session, task)
    return task


def enqueue_review(task_id: str, run_id: str) -> None:
    from redis import Redis
    from rq import Queue

    from vendorlens.config import get_settings

    queue = Queue("vendorlens", connection=Redis.from_url(get_settings().redis_url))
    queue.enqueue("vendorlens.tasks.execute_review_job", task_id, job_id=run_id, job_timeout=3600)


def execute_review_job(task_id: str) -> None:
    from vendorlens.db import SessionLocal

    run_review_task(task_id, SessionLocal, build_runtime_workflow)


@contextmanager
def build_runtime_workflow(session_factory):
    from contextlib import ExitStack

    import httpx

    from vendorlens.checkpoint import postgres_checkpointer, sqlite_checkpointer
    from vendorlens.coordinator import DatabaseRetrievalCoordinator
    from vendorlens.indexes import DatabaseParentRetriever, PersistentHybridRetriever
    from vendorlens.model_routing import RoutedVendorReviewExecutor
    from vendorlens.retrieval_runtime import retrieval_components

    settings = get_settings()
    with ExitStack() as stack:
        saver = stack.enter_context(
            postgres_checkpointer(settings.database_url)
            if settings.database_url.startswith("postgresql")
            else sqlite_checkpointer(settings.storage_root / "checkpoints.sqlite")
        )
        bm25, vector, reranker = retrieval_components(settings)
        retriever = DatabaseParentRetriever(PersistentHybridRetriever(bm25, vector, reranker))
        yield ReviewWorkflow(
            DatabaseRetrievalCoordinator(session_factory, retriever),
            RoutedVendorReviewExecutor(session_factory, settings,
                                       client=stack.enter_context(httpx.Client())),
            checkpointer=saver,
        )


def run_review_task(task_id: str, session_factory: Callable[[], Session], workflow_factory) -> bool:
    """Claim once; persist a report and its terminal event in one transaction.

    Interrupted workers resume from a persistent checkpoint under a new fenced lease.
    Never persist exception text, prompts or provider credentials in progress/Trace.
    """
    events = RunEventStore()
    started = monotonic()
    owner = new_id()
    lease_seconds = get_settings().task_lease_seconds
    with session_factory() as session:
        claimed = session.execute(
            update(ResearchTask)
            .where(ResearchTask.id == task_id, ResearchTask.status == "queued")
            .values(
                status="running",
                lease_owner=owner,
                lease_expires_at=utcnow() + timedelta(seconds=lease_seconds),
                attempt=ResearchTask.attempt + 1,
            )
        ).rowcount
        if not claimed:
            session.rollback()
            return False
        task = session.get(ResearchTask, task_id)
        snapshot = dict(task.request_snapshot)
        run_id = task.run_id
        resume = task.resume_requested
        trace = TraceSpan(trace_id=task.trace_id, name="vendor_review", kind="workflow")
        session.add(trace)
        events.append(session, task_id, "run.started", {"run_id": run_id})
        session.flush()
        trace_id = trace.id
        run_trace_id = task.trace_id
        session.commit()
    try:
        state = dict(snapshot, task_id=task_id)
        state["requirements"] = [
            RequirementItem.model_validate(r) for r in snapshot["requirements"]
        ]
        state["queries_by_domain"] = {
            ReviewDomain(k): v for k, v in snapshot["queries_by_domain"].items()
        }
        state["weights"] = ReviewWeights.model_validate(snapshot["weights"])
        recorder = TraceRecorder(session_factory, run_trace_id, trace_id)
        with (
            LeaseHeartbeat(session_factory, task_id, owner, lease_seconds),
            recorder.activate(),
            workflow_factory(session_factory) as workflow,
        ):
            from vendorlens.checkpoint import FencedCheckpointSaver

            workflow.graph.checkpointer = FencedCheckpointSaver(
                workflow.graph.checkpointer, session_factory, task_id, owner
            )
            config = {"configurable": {"thread_id": run_id}}
            checkpoint = workflow.graph.get_state(config)
            graph_input = None if resume and checkpoint.values else state
            for update_data in workflow.graph.stream(graph_input, config, stream_mode="updates"):
                with session_factory() as session:
                    own_task(session, task_id, owner)
                    for node, values in update_data.items():
                        events.append(
                            session,
                            task_id,
                            "node.completed",
                            {
                                "node": node,
                                "status": values.get("status"),
                            },
                        )
                    session.commit()
            result = workflow.graph.get_state(config).values
        with session_factory() as session:
            task = own_task(session, task_id, owner)
            for vendor_id, assessment in result["assessments"].items():
                for domain, item in assessment.domains.items():
                    payload = item.model_dump(mode="json")
                    review_input = result.get("review_inputs", {}).get(vendor_id)
                    payload["source_evidence"] = [
                        evidence.model_dump(mode="json")
                        for evidence in (review_input.evidence if review_input else [])
                        if evidence.id in item.evidence_ids
                    ]
                    session.add(
                        DomainAssessmentRecord(
                            task_id=task_id,
                            vendor_id=vendor_id,
                            domain=domain.value,
                            status=item.status.value,
                            payload=payload,
                            missing_information=item.missing_information,
                            conflicts=item.conflicts,
                            revision_count=item.revision_count,
                        )
                    )
            task.status = result["status"]
            task.lease_owner = None
            task.lease_expires_at = None
            task.resume_requested = False
            session.add(
                AnalysisReport(
                    task_id=task_id,
                    status=task.status,
                    content=result["report"].model_dump(mode="json"),
                )
            )
            from vendorlens.conversation_memory import ConversationMemory

            ConversationMemory().record_result(session, task, result)
            _finish_trace(session, trace_id, task.status, started)
            events.append(session, task_id, "run.finished", {"status": task.status})
            session.commit()
    except TaskLeaseLost:
        return False
    except Exception as error:
        with session_factory() as session:
            try:
                task = own_task(session, task_id, owner)
            except TaskLeaseLost:
                return False
            task.status = "failed"
            task.lease_owner = None
            task.lease_expires_at = None
            _finish_trace(session, trace_id, "failed", started)
            session.get(TraceSpan, trace_id).error = type(error).__name__
            events.append(session, task_id, "run.failed", {"error": "review_execution_failed"})
            session.commit()
        raise RuntimeError("review execution failed; inspect task trace") from None
    return True


def _finish_trace(session: Session, span_id: str, status: str, started: float) -> None:
    span = session.get(TraceSpan, span_id)
    span.status = status
    span.ended_at = utcnow()
    span.duration_ms = int((monotonic() - started) * 1000)
