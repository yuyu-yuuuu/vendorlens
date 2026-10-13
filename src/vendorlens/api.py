from collections.abc import Callable
from time import sleep
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from vendorlens import __version__
from vendorlens.api_contracts import (
    REQUEST_INVALID,
    TASK_EXAMPLE,
    TASK_NOT_FOUND,
    ErrorResponse,
    HealthResponse,
    InvalidRequestResponse,
    QueueErrorResponse,
    ReportResponse,
    TaskResponse,
    TraceResponse,
    documented_error,
)
from vendorlens.auth import authenticate
from vendorlens.db import get_session
from vendorlens.events import RunEventStore, format_sse
from vendorlens.management_api import router as management_router
from vendorlens.models import AnalysisReport, ResearchTask, TraceSpan
from vendorlens.observability import cost_summary, prometheus_metrics
from vendorlens.recovery import prepare_recovery
from vendorlens.report_api import router as report_router
from vendorlens.runtime_api import router as runtime_router
from vendorlens.tasks import (
    TERMINAL_STATUSES,
    ReviewTaskRequest,
    create_review_task,
    enqueue_review,
)
from vendorlens.tracing import safe_attributes

app = FastAPI(
    dependencies=[Depends(authenticate)],
    title="VendorLens 接口文档",
    version=__version__,
    description="""证据驱动供应商评审服务。\n\n
**使用顺序**：准备项目、供应商、需求目录和已索引资料 → 创建任务 → 查询进度 → 读取报告和 Trace。

已提供项目、供应商、需求、配置与上传/索引管理，并支持过期任务恢复。
创建任务返回 202 不代表评审已完成；Worker 需单独启动。

**注意**：Try it out 会调用真实接口。创建任务可能触发付费模型；示例 ID 为占位值，
请替换为数据库中真实 ID。非 development 环境必需共享 Bearer Key；
未配置时业务接口返回 503。仅 development 可不配置密钥，用于可信本地环境。

SSE 为长连接，建议使用终端 `curl -N`；它是节点进度，不是模型逐字输出。
""",
    openapi_tags=[
        {"name": "项目与资料管理", "description": "项目、供应商、需求、配置和不可变资料版本。"},
        {"name": "运行维护", "description": "租约恢复和聚合指标。"},
        {"name": "健康检查", "description": "检查 API 与数据库连接，不检查所有外部依赖。"},
        {"name": "评审任务", "description": "创建异步任务，查询状态与结构化报告。"},
        {"name": "进度事件", "description": "持久化 SSE 事件流，支持断线续读。"},
        {"name": "执行追踪", "description": "父子执行记录、耗时、错误类型与 Token 汇总。"},
    ],
    swagger_ui_parameters={
        "displayRequestDuration": True,
        "filter": True,
        "docExpansion": "list",
        "defaultModelsExpandDepth": 0,
        "persistAuthorization": False,
    },
)
app.include_router(runtime_router)
app.include_router(report_router)


DatabaseSession = Annotated[Session, Depends(get_session)]
ProjectId = Annotated[
    str, Path(description="已存在的评审项目 ID", examples=[TASK_EXAMPLE["project_id"]])
]
TaskId = Annotated[
    str, Path(description="创建任务返回的 task_id", examples=[TASK_EXAMPLE["task_id"]])
]


@app.get(
    "/health",
    tags=["健康检查"],
    summary="检查 API 与数据库",
    response_model=HealthResponse,
    description="执行数据库 SELECT 1。成功不代表 Redis、Chroma 或模型连接正常。",
    responses={
        500: {
            "description": "数据库检查异常（服务未配置统一异常响应）",
            "content": {
                "text/plain": {"schema": {"type": "string"}, "example": "Internal Server Error"}
            },
        }
    },
)
def health(session: DatabaseSession) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__, "database": "ok"}


def get_review_enqueuer():
    return enqueue_review


def task_summary(task: ResearchTask) -> dict:
    return {
        "task_id": task.id,
        "run_id": task.run_id,
        "trace_id": task.trace_id,
        "project_id": task.project_id,
        "status": task.status,
        "attempt": task.attempt,
        "lease_expires_at": task.lease_expires_at,
    }


@app.post(
    "/projects/{project_id}/tasks",
    status_code=202,
    tags=["评审任务"],
    summary="创建并入队供应商评审任务",
    response_model=TaskResponse,
    description="保存需求、权重、成本周期和查询快照，再入队执行。文档版本在 Worker 检索时冻结。"
    "供应商必须属于该项目，项目必须已有需求目录。相同请求再次提交会创建新任务。"
    "可能调用付费模型；请勿直接执行占位 ID 示例。",
    responses={
        202: {
            "description": "任务已接受；通常 queued，快速 Worker 可能已更新状态",
            "content": {"application/json": {"example": TASK_EXAMPLE}},
        },
        404: documented_error(ErrorResponse, "项目不存在", {"detail": "project not found"}),
        422: REQUEST_INVALID,
        503: documented_error(
            QueueErrorResponse,
            "任务已创建但入队失败",
            {"detail": {**TASK_EXAMPLE, "status": "enqueue_failed", "error": "queue_unavailable"}},
        ),
    },
)
def submit_review(
    project_id: ProjectId,
    request: ReviewTaskRequest,
    session: DatabaseSession,
    enqueuer: Annotated[Callable[[str, str], None], Depends(get_review_enqueuer)],
):
    try:
        task = create_review_task(session, project_id, request)
    except LookupError as error:
        raise HTTPException(404, str(error)) from None
    except ValueError:
        raise HTTPException(
            422, "invalid project review configuration or vendor selection"
        ) from None
    session.commit()
    try:
        enqueuer(task.id, task.run_id)
        from vendorlens.models import TaskDispatch, utcnow

        session.get(TaskDispatch, task.id).delivered_at = utcnow()
        session.commit()
    except Exception:
        # A network timeout can leave enqueue outcome uncertain. Do not overwrite a
        # running/completed task if the worker already claimed the accepted job.
        session.expire_all()
        task = session.scalar(
            select(ResearchTask).where(ResearchTask.id == task.id).with_for_update()
        )
        if task.status == "queued":
            task.status = "enqueue_failed"
            RunEventStore().append(
                session, task.id, "run.enqueue_failed", {"error": "queue_unavailable"}
            )
            session.commit()
            raise HTTPException(503, {"error": "queue_unavailable", **task_summary(task)}) from None
    session.refresh(task)
    return task_summary(task)


@app.get(
    "/tasks/{task_id}",
    tags=["评审任务"],
    summary="查询任务状态",
    response_model=TaskResponse,
    description="读取任务状态，不重新执行 Agent。Worker 使用心跳租约，"
    "过期后可恢复并继续持久化 checkpoint。",
    responses={404: TASK_NOT_FOUND},
)
def get_task(task_id: TaskId, session: DatabaseSession):
    task = session.get(ResearchTask, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return task_summary(task)


@app.get(
    "/tasks/{task_id}/report",
    tags=["评审任务"],
    summary="读取最终评审报告",
    response_model=ReportResponse,
    description="任务有报告时返回结构化结果。"
    "报告 status 为 completed 不表示材料无缺口。报告不存在时返回 409，"
    "包括排队中和执行异常未生成报告的情况。",
    responses={
        404: TASK_NOT_FOUND,
        409: documented_error(
            ErrorResponse, "报告尚未生成或执行异常未生成报告", {"detail": "report is not available"}
        ),
    },
)
def get_report(task_id: TaskId, session: DatabaseSession):
    if session.get(ResearchTask, task_id) is None:
        raise HTTPException(404, "task not found")
    report = session.scalar(select(AnalysisReport).where(AnalysisReport.task_id == task_id))
    if report is None:
        raise HTTPException(409, "report is not available")
    return {"task_id": task_id, "status": report.status, "report": report.content}


@app.get(
    "/tasks/{task_id}/trace",
    tags=["执行追踪"],
    summary="查询父子 Trace 与 Token 用量",
    response_model=TraceResponse,
    responses={404: TASK_NOT_FOUND},
    description="按开始时间返回跨度，以 parent_span_id 重建执行树。排队任务可能返回空 spans。"
    "Token 只汇总完整 usage 的请求；usage_complete=false 时不是完整总消耗，"
    "无已报告用量时为 null。费用仅按显式配置单价估算，非账单；不提供提示词或密钥。",
)
def get_trace(task_id: TaskId, session: DatabaseSession):
    task = session.get(ResearchTask, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    spans = list(
        session.scalars(
            select(TraceSpan)
            .where(TraceSpan.trace_id == task.trace_id)
            .order_by(TraceSpan.started_at, TraceSpan.id)
        )
    )
    models = [span for span in spans if span.kind == "model"]
    reported = [span for span in models if span.attributes.get("usage_reported")]
    summary = {
        **cost_summary(models),
        "model_calls": len(models),
        "tool_calls": sum(span.kind == "tool" for span in spans),
        "usage_reported_calls": len(reported),
        "usage_complete": bool(models) and len(reported) == len(models),
        **{
            key: sum(span.attributes.get(key, 0) for span in reported) if reported else None
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
    }
    return {
        "task_id": task_id,
        "trace_id": task.trace_id,
        "summary": summary,
        "spans": [
            {
                "id": span.id,
                "parent_span_id": span.parent_span_id,
                "name": span.name,
                "kind": span.kind,
                "status": span.status,
                "started_at": span.started_at,
                "ended_at": span.ended_at,
                "duration_ms": span.duration_ms,
                "attributes": safe_attributes(span.attributes),
                "error": span.error,
            }
            for span in spans
        ],
    }


def stream_task_events(session_factory, task_id: str, sequence: int):
    while True:
        with session_factory() as session:
            # Read task first: terminal status and terminal event commit together.
            task = session.get(ResearchTask, task_id)
            terminal = task is None or task.status in TERMINAL_STATUSES
            events = RunEventStore().after(session, task_id, sequence)
        for event in events:
            sequence = event.sequence
            yield format_sse(event)
        if terminal:
            return
        yield ": keep-alive\n\n"
        sleep(1)


@app.get(
    "/tasks/{task_id}/events",
    tags=["进度事件"],
    summary="订阅任务进度（SSE，可续读）",
    response_class=StreamingResponse,
    description="返回 text/event-stream，非 JSON 数组。每条事件包含 id/event/data；"
    "Last-Event-ID 优先于 after，只读取更大的序号，不重跑任务。终态回放完成后关闭连接，"
    "执行中每秒轮询并发送 keep-alive 注释。Swagger 对长连接显示有限，建议用 curl -N。\n\n"
    "```bash\ncurl -N -H 'Last-Event-ID: 5' http://127.0.0.1:8000/tasks/你的任务ID/events\n```",
    responses={
        200: {
            "description": "SSE 节点进度；终态后关闭",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": "id: 6\nevent: node.completed\n"
                    'data: {"node":"validate_domains","status":"validated"}\n\n',
                }
            },
        },
        404: TASK_NOT_FOUND,
        422: documented_error(
            InvalidRequestResponse,
            "事件序号须为非负整数",
            {
                "detail": [
                    {
                        "loc": ["query", "after"],
                        "msg": "Input should be greater than or equal to 0",
                        "type": "greater_than_equal",
                    }
                ]
            },
        ),
    },
)
def get_events(
    task_id: TaskId,
    session: DatabaseSession,
    after: Annotated[int, Query(ge=0, description="已读事件序号，从下一条读取；默认为 0")] = 0,
    last_event_id: Annotated[
        int | None, Header(ge=0, description="断线续读序号，优先于 after")
    ] = None,
):
    if session.get(ResearchTask, task_id) is None:
        raise HTTPException(404, "task not found")
    # Fresh short-lived sessions; no request transaction stays open while polling.
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    session.rollback()
    return StreamingResponse(
        stream_task_events(factory, task_id, last_event_id if last_event_id is not None else after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.include_router(management_router)


@app.get(
    "/metrics", tags=["运行维护"], summary="读取持久化聚合 Prometheus 指标", response_class=Response
)
def get_metrics(session: DatabaseSession):
    return Response(prometheus_metrics(session), media_type="text/plain; version=0.0.4")


@app.post(
    "/tasks/{task_id}/retry",
    status_code=202,
    tags=["运行维护"],
    summary="恢复过期、异常或入队失败的任务",
    response_model=TaskResponse,
    responses={
        404: TASK_NOT_FOUND,
        409: {"description": "任务正在执行或已有报告"},
        503: {"description": "恢复入队失败，任务 ID 保留"},
    },
)
def retry_task(
    task_id: TaskId,
    session: DatabaseSession,
    enqueuer: Annotated[Callable[[str, str], None], Depends(get_review_enqueuer)],
):
    try:
        task = prepare_recovery(session, task_id)
    except LookupError:
        raise HTTPException(404, "task not found") from None
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    session.commit()
    try:
        from vendorlens.models import TaskDispatch, utcnow

        dispatch = session.get(TaskDispatch, task.id)
        enqueuer(task.id, dispatch.job_id)
        dispatch.delivered_at = utcnow()
        session.commit()
    except Exception:
        from sqlalchemy import update

        changed = session.execute(
            update(ResearchTask)
            .where(
                ResearchTask.id == task.id,
                ResearchTask.status == "queued",
            )
            .values(status="enqueue_failed")
        ).rowcount
        if changed:
            RunEventStore().append(
                session, task.id, "run.enqueue_failed", {"error": "queue_unavailable"}
            )
            session.commit()
            raise HTTPException(503, {"error": "queue_unavailable", "task_id": task.id}) from None
    session.refresh(task)
    return task_summary(task)
