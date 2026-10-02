"""Public response contracts used by FastAPI and the generated OpenAPI document."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from vendorlens.schemas import FinalReport

TaskStatus = Literal[
    "queued", "running", "completed", "partially_completed", "failed", "enqueue_failed"
]


class HealthResponse(BaseModel):
    status: Literal["ok"] = Field(description="服务可响应；不代表 Redis、Chroma 或模型可用")
    version: str = Field(description="应用版本", examples=["0.1.0"])
    database: Literal["ok"] = Field(description="数据库 SELECT 1 检查通过")


class TaskResponse(BaseModel):
    task_id: str = Field(description="业务任务 ID，用于查询状态、报告、事件和 Trace")
    run_id: str = Field(description="RQ job_id 与 LangGraph checkpoint thread_id")
    trace_id: str = Field(description="关联本次执行的所有 TraceSpan")
    project_id: str = Field(description="所属评审项目 ID")
    status: TaskStatus = Field(
        description="queued=排队；running=执行中；completed=完成；"
        "partially_completed=含降级或失败领域；failed=失败；enqueue_failed=入队失败"
    )


class ReportResponse(BaseModel):
    task_id: str = Field(description="评审任务 ID")
    status: Literal["completed", "partially_completed", "failed"] = Field(
        description="报告业务终态；failed 任务可能有失败报告，也可能因执行异常没有报告"
    )
    report: FinalReport = Field(description="报告摘要、比较矩阵、风险、冲突、缺失信息及证据矩阵")


class TraceSummary(BaseModel):
    model_calls: int = Field(ge=0, description="记录到的模型请求数，包含失败请求")
    tool_calls: int = Field(ge=0, description="记录到的工具调用尝试数，包含失败调用")
    usage_reported_calls: int = Field(ge=0, description="返回完整 Token usage 的模型请求数")
    usage_complete: bool = Field(description="至少有一次模型调用且每次调用都返回完整 usage")
    prompt_tokens: int | None = Field(description="仅合计完整 usage 的输入 Token；没有则为 null")
    completion_tokens: int | None = Field(
        description="仅合计完整 usage 的输出 Token；没有则为 null"
    )
    total_tokens: int | None = Field(description="仅合计完整 usage 的总 Token，不是账单金额")


class TraceSpanResponse(BaseModel):
    id: str = Field(description="当前跨度 ID")
    parent_span_id: str | None = Field(description="父跨度 ID；根跨度为 null")
    name: str = Field(description="操作名，例如 analyze_vendors、chat_completion、calculate_tco")
    kind: str = Field(description="workflow/node/retrieval/agent/model/tool/fallback")
    status: str = Field(description="跨度执行状态；模型请求完成不等于 Agent 输出通过校验")
    started_at: datetime = Field(description="开始时间；生产 PostgreSQL 保存 UTC 时间")
    ended_at: datetime | None = Field(description="结束时间；执行中或硬中断时可能为 null")
    duration_ms: int | None = Field(description="耗时毫秒；父跨度包含子操作，不能简单相加")
    attributes: dict[str, str | int | float | bool] = Field(
        description="白名单元数据；不含提示词、密钥、工具原始参数或结果"
    )
    error: str | None = Field(description="异常类型；不保存原始异常文本")


class TraceResponse(BaseModel):
    task_id: str
    trace_id: str
    summary: TraceSummary
    spans: list[TraceSpanResponse] = Field(description="按开始时间排序，用 parent_span_id 构建树")


class ErrorResponse(BaseModel):
    detail: str = Field(description="业务错误说明", examples=["task not found"])


class ValidationIssue(BaseModel):
    loc: list[str | int] = Field(
        description="出错位置，例如 body/vendor_ids 或 header/last-event-id"
    )
    msg: str = Field(description="校验错误说明")
    type: str = Field(description="错误类型")
    input: Any = Field(default=None, description="可能携带导致错误的输入值")
    ctx: dict[str, Any] | None = Field(default=None, description="可选校验上下文")


class InvalidRequestResponse(BaseModel):
    detail: str | list[ValidationIssue] = Field(
        description="项目配置/归属错误为字符串；请求字段校验错误为错误数组"
    )


class EnqueueFailure(TaskResponse):
    error: Literal["queue_unavailable"]


class QueueErrorResponse(BaseModel):
    detail: EnqueueFailure = Field(description="任务已创建但未确认入队，保留 task_id 供查询")


TASK_EXAMPLE = {
    "task_id": "11111111-1111-4111-8111-111111111111",
    "run_id": "22222222-2222-4222-8222-222222222222",
    "trace_id": "33333333-3333-4333-8333-333333333333",
    "project_id": "44444444-4444-4444-8444-444444444444",
    "status": "queued",
}


def documented_error(model, description: str, example: dict) -> dict:
    return {
        "model": model,
        "description": description,
        "content": {"application/json": {"example": example}},
    }


TASK_NOT_FOUND = documented_error(ErrorResponse, "任务不存在", {"detail": "task not found"})
REQUEST_INVALID = documented_error(
    InvalidRequestResponse,
    "字段校验失败，或供应商归属/需求目录/评审配置不合法",
    {"detail": "invalid project review configuration or vendor selection"},
)
