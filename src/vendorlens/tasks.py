from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

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
    utcnow,
)
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
        "currency": config.currency if config else "CNY",
        "comparison_months": config.comparison_months if config else 36,
        "required_cost_categories": ["software", "implementation", "maintenance"],
        "required_commitments": config.commitment_checklist if config else [],
        "project_context": f"{project.description}\n评审目标：{request.objective}",
        "weights": weights.model_dump(mode="json"),
    }
    if snapshot["comparison_months"] <= 0:
        raise ValueError("comparison months must be positive")
    task = ResearchTask(
        project_id=project_id, objective=request.objective, request_snapshot=snapshot
    )
    session.add(task)
    session.flush()
    RunEventStore().append(session, task.id, "run.queued", {"run_id": task.run_id})
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

    from vendorlens.checkpoint import postgres_checkpointer
    from vendorlens.config import get_settings
    from vendorlens.coordinator import DatabaseRetrievalCoordinator
    from vendorlens.indexes import (
        ChromaVectorIndex,
        DatabaseParentRetriever,
        PersistentBM25Index,
        PersistentHybridRetriever,
    )
    from vendorlens.model_analyzer import build_vendor_review_executor

    settings = get_settings()
    with ExitStack() as stack:
        saver = (
            stack.enter_context(postgres_checkpointer(settings.database_url))
            if settings.database_url.startswith("postgresql")
            else None
        )
        retriever = DatabaseParentRetriever(
            PersistentHybridRetriever(
                PersistentBM25Index(settings.storage_root / "indexes" / "bm25.json"),
                ChromaVectorIndex.from_url(settings.chroma_url),
            )
        )
        yield ReviewWorkflow(
            DatabaseRetrievalCoordinator(session_factory, retriever),
            build_vendor_review_executor(settings, client=stack.enter_context(httpx.Client())),
            checkpointer=saver,
        )


def run_review_task(task_id: str, session_factory: Callable[[], Session], workflow_factory) -> bool:
    """Claim once; persist a report and its terminal event in one transaction.

    Hard-killed workers require explicit recovery (not automatic replay).
    Never persist exception text, prompts or provider credentials in progress/Trace.
    """
    events = RunEventStore()
    started = monotonic()
    with session_factory() as session:
        claimed = session.execute(
            update(ResearchTask)
            .where(ResearchTask.id == task_id, ResearchTask.status == "queued")
            .values(status="running")
        ).rowcount
        if not claimed:
            session.rollback()
            return False
        task = session.get(ResearchTask, task_id)
        snapshot = dict(task.request_snapshot)
        run_id = task.run_id
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
        with recorder.activate(), workflow_factory(session_factory) as workflow:
            config = {"configurable": {"thread_id": run_id}}
            for update_data in workflow.graph.stream(state, config, stream_mode="updates"):
                with session_factory() as session:
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
            task = session.get(ResearchTask, task_id)
            for vendor_id, assessment in result["assessments"].items():
                for domain, item in assessment.domains.items():
                    session.add(
                        DomainAssessmentRecord(
                            task_id=task_id,
                            vendor_id=vendor_id,
                            domain=domain.value,
                            status=item.status.value,
                            payload=item.model_dump(mode="json"),
                            missing_information=item.missing_information,
                            conflicts=item.conflicts,
                            revision_count=item.revision_count,
                        )
                    )
            task.status = result["status"]
            session.add(
                AnalysisReport(
                    task_id=task_id,
                    status=task.status,
                    content=result["report"].model_dump(mode="json"),
                )
            )
            _finish_trace(session, trace_id, task.status, started)
            events.append(session, task_id, "run.finished", {"status": task.status})
            session.commit()
    except Exception as error:
        with session_factory() as session:
            task = session.get(ResearchTask, task_id)
            task.status = "failed"
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
