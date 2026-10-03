"""Conversation, explicit preferences, Skills reload and route audit APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from vendorlens.config import get_settings
from vendorlens.conversation_memory import ConversationMemory, Preferences
from vendorlens.db import get_session
from vendorlens.model_adapter import ModelConfigurationError
from vendorlens.model_routing import HealthRouter
from vendorlens.models import ConversationSession, Project, RoutingAttempt, Vendor
from vendorlens.skills import SkillReloadError, skill_manager

router = APIRouter(tags=["对话与运行策略"])
Database = Annotated[Session, Depends(get_session)]
UserId = Annotated[str, Query(min_length=1, max_length=100)]


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(default="local", min_length=1, max_length=100)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=8000)


def project_exists(db, project_id):
    if db.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")


def scoped(db, project_id, session_id, user_id):
    try:
        return ConversationMemory.scoped_session(db, session_id, project_id, user_id)
    except LookupError:
        raise HTTPException(404, "conversation session not found in this scope") from None


@router.post("/projects/{project_id}/sessions", status_code=201, summary="创建评审会话")
def create_session(project_id: str, request: SessionRequest, db: Database):
    project_exists(db, project_id)
    row = ConversationSession(project_id=project_id, user_id=request.user_id)
    db.add(row)
    db.commit()
    return {"session_id": row.id, "project_id": project_id, "user_id": row.user_id}


@router.post(
    "/projects/{project_id}/sessions/{session_id}/messages",
    status_code=201,
    summary="保存用户讨论；需要评审时再创建关联 session_id 的任务",
)
def append_message(
    project_id: str,
    session_id: str,
    request: MessageRequest,
    db: Database,
    user_id: UserId = "local",
):
    row = scoped(db, project_id, session_id, user_id)
    try:
        message = ConversationMemory().append(db, row, "user", request.content)
    except ValueError:
        raise HTTPException(422, "message must not be blank") from None
    db.commit()
    return {"message_id": message.id, "sequence": message.sequence}


@router.get("/projects/{project_id}/sessions/{session_id}/context", summary="查看三层记忆上下文")
def conversation_context(
    project_id: str,
    session_id: str,
    db: Database,
    user_id: UserId = "local",
    query: str = "评审历史",
):
    row = scoped(db, project_id, session_id, user_id)
    vendors = list(db.scalars(select(Vendor.id).where(Vendor.project_id == project_id)))
    return ConversationMemory().context(db, row, query, vendors)


@router.put("/projects/{project_id}/preferences", summary="显式更新长期偏好；消息不自动提取偏好")
def set_preferences(project_id: str, request: Preferences, db: Database, user_id: UserId = "local"):
    project_exists(db, project_id)
    result = ConversationMemory().set_preferences(db, project_id, user_id, request.model_dump())
    db.commit()
    return result


@router.get("/projects/{project_id}/preferences", summary="读取项目内用户偏好")
def get_preferences(project_id: str, db: Database, user_id: UserId = "local"):
    project_exists(db, project_id)
    return ConversationMemory.preferences(db, project_id, user_id)


@router.get("/skills", summary="查看已加载 Skills 版本和内容哈希")
def get_skills():
    manager = skill_manager(get_settings().skills_root)
    try:
        manager.reload(force=False)
    except SkillReloadError:
        if manager.summary()["generation"] is None:
            raise HTTPException(422, "invalid skill files") from None
    return manager.summary()


@router.post("/skills/reload", summary="原子热加载；非法文件保留上一版有效规范")
def reload_skills():
    try:
        return skill_manager(get_settings().skills_root).reload()
    except SkillReloadError:
        raise HTTPException(422, "invalid skill files; previous rules retained") from None


@router.get("/routing/health", summary="查看模型端点健康分、冷却和探测状态")
def routing_health(db: Database):
    try:
        return {
            "endpoints": HealthRouter(sessionmaker(bind=db.get_bind()), get_settings()).summary()
        }
    except ModelConfigurationError:
        return {"endpoints": [], "configured": False}


@router.get("/tasks/{task_id}/routing", summary="查看该任务的路由选择与质量反馈（不含密钥）")
def task_routing(task_id: str, db: Database):
    from vendorlens.models import ResearchTask

    if db.get(ResearchTask, task_id) is None:
        raise HTTPException(404, "task not found")
    return {
        "attempts": [
            {
                "id": r.id,
                "vendor_id": r.vendor_id,
                "endpoint_id": r.endpoint_id,
                "decision": r.decision,
                "status": r.status,
                "error_type": r.error_type,
                "quality_failure_rate": r.quality_failure_rate,
                "latency_ms": r.latency_ms,
                "request_latencies": r.request_latencies,
                "estimated_cost": r.estimated_cost,
                "price_currency": r.price_currency,
            }
            for r in db.scalars(
                select(RoutingAttempt)
                .where(RoutingAttempt.task_id == task_id)
                .order_by(RoutingAttempt.created_at)
            )
        ]
    }
