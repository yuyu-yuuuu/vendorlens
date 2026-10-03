from datetime import timedelta

import chromadb
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_tasks import seeded as seeded
from test_tasks import workflow_factory

from vendorlens.config import Settings
from vendorlens.conversation_memory import ConversationMemory, vendor_memory
from vendorlens.models import (
    ConversationMessage,
    ConversationSession,
    Document,
    DocumentVersion,
    EpisodicMemory,
    Project,
    ResearchTask,
    ReviewConfig,
    utcnow,
)
from vendorlens.skills import skill_manager
from vendorlens.tasks import ReviewTaskRequest, create_review_task, run_review_task


def memory_settings(**kwargs):
    return Settings(_env_file=None, memory_recent_messages=2, memory_compress_at=4, **kwargs)


def conversation(db, *, project="p", user="alice"):
    if db.get(Project, project) is None:
        db.add(Project(id=project, name=project))
        db.flush()
    row = ConversationSession(project_id=project, user_id=user)
    db.add(row)
    db.flush()
    return row


def test_compression_retains_recent_and_user_project_isolation(session):
    memory = ConversationMemory(memory_settings())
    first = conversation(session)
    second = conversation(session)
    other = conversation(session, user="bob")
    for i in range(4):
        memory.append(session, first, "user", f"比较维护费用第{i}轮")
    context = memory.context(session, second, "维护费用", [])
    assert context["recent_messages"] == []
    assert "第0轮" in context["episodes"][0]["summary"]
    assert [m["sequence"] for m in memory.recent(session, first)] == [3, 4]
    assert not memory.context(session, other, "维护费用", [])["episodes"]
    with pytest.raises(LookupError):
        memory.scoped_session(session, first.id, "other-project", "alice")
    assert not memory.preferences(session, "p", "alice")


def test_old_version_episode_is_excluded_before_vector_retrieval(session):
    row = conversation(session)
    session.add(Document(id="doc", project_id="p", logical_name="报价", media_type="text/plain"))
    session.flush()
    session.add_all(
        [
            DocumentVersion(
                id="v1",
                document_id="doc",
                version_number=1,
                file_hash="a" * 64,
                storage_key="old",
                status="indexed",
            ),
            DocumentVersion(
                id="v2",
                document_id="doc",
                version_number=2,
                file_hash="b" * 64,
                storage_key="new",
                status="indexed",
            ),
        ]
    )
    session.add_all(
        [
            EpisodicMemory(
                id="stale",
                project_id="p",
                user_id="alice",
                session_id=row.id,
                summary="旧报价维护费用",
                source_versions=["v1"],
                vendor_id="a",
            ),
            EpisodicMemory(
                id="current",
                project_id="p",
                user_id="alice",
                session_id=row.id,
                summary="新报价维护费用",
                source_versions=["v2"],
                vendor_id="a",
            ),
            EpisodicMemory(
                id="competitor",
                project_id="p",
                user_id="alice",
                session_id=row.id,
                summary="竞争供应商报价",
                source_versions=["v2"],
                vendor_id="b",
            ),
        ]
    )
    session.flush()
    collection = chromadb.EphemeralClient().get_or_create_collection("memory-version-test")
    memory = ConversationMemory(memory_settings(), vector_collection=collection)
    context = memory.context(session, row, "维护费用", ["a"])
    assert [e["id"] for e in context["episodes"]] == ["current"]
    assert context["stale_episodes_excluded"] == 1
    assert context["retrieval_backend"] == "chroma_hash"
    assert collection.get()["ids"] == ["current"]
    assert not vendor_memory(context, "b")["episodes"]
    assert not vendor_memory(context, "a", ["new-worker-version"])["episodes"]


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttl = None

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, payload):
        self.ttl = ttl
        self.values[key] = payload


def test_redis_ttl_cache_refresh_and_database_fallback(session):
    row = conversation(session)
    redis = FakeRedis()
    memory = ConversationMemory(memory_settings(), redis_client=redis)
    memory.append(session, row, "user", "比较实施费用")
    expected = memory.recent(session, row)
    assert redis.ttl == 86400
    assert memory.recent(session, row) == expected
    assert memory.cache_status == "redis_hit"
    memory.append(session, row, "user", "再看维护费用")
    assert len(memory.recent(session, row)) == 2
    assert memory.cache_status == "redis_refreshed"

    class BrokenRedis(FakeRedis):
        def get(self, key):
            raise OSError("unavailable")

    fallback = ConversationMemory(memory_settings(), redis_client=BrokenRedis())
    assert len(fallback.recent(session, row)) == 2
    assert fallback.cache_status == "redis_unavailable"
    for message in session.scalars(select(ConversationMessage)):
        message.created_at = utcnow() - timedelta(days=2)
    session.flush()
    assert memory.recent(session, row) == []


def test_chroma_failure_uses_local_retrieval(session):
    row = conversation(session)
    session.add(
        EpisodicMemory(
            project_id="p", user_id="alice", session_id=row.id, summary="维护费缺失需要补充报价"
        )
    )
    session.flush()

    class BrokenVector:
        def upsert(self, **kwargs):
            raise OSError("unavailable")

    memory = ConversationMemory(memory_settings(), vector_collection=BrokenVector())
    context = memory.context(session, row, "报价", [])
    assert context["episodes"]
    assert context["retrieval_backend"] == "chroma_unavailable_local_hybrid"


def test_compressed_assistant_history_keeps_source_version_dependencies(session):
    row = conversation(session)
    task = ResearchTask(project_id="p", objective="比较报价")
    session.add(task)
    session.flush()
    session.add(
        EpisodicMemory(
            project_id="p",
            user_id="alice",
            session_id=row.id,
            task_id=task.id,
            vendor_id="a",
            summary="旧评审摘要",
            source_versions=["old-version"],
            evidence_ids=["old-evidence"],
        )
    )
    session.flush()
    memory = ConversationMemory(memory_settings())
    memory.append(session, row, "assistant", "历史成本结论", task_id=task.id)
    for _ in range(3):
        memory.append(session, row, "user", "追问费用")
    compressed = session.scalar(select(EpisodicMemory).where(EpisodicMemory.task_id.is_(None)))
    assert compressed.source_versions == ["old-version"]
    assert compressed.evidence_ids == ["old-evidence"]
    assert not memory.context(session, row, "成本", ["a"])["episodes"]


def test_task_freezes_context_skills_preferences_and_records_result(seeded, tmp_path, monkeypatch):
    settings = Settings(_env_file=None, skills_root=tmp_path)
    monkeypatch.setattr("vendorlens.tasks.get_settings", lambda: settings)
    row = conversation(seeded, project="smoke-project")
    memory = ConversationMemory(settings)
    memory.append(seeded, row, "user", "只考虑本地部署")
    memory.set_preferences(seeded, row.project_id, "alice", {"comparison_months": 60})
    policy = tmp_path / "policy.json"
    policy.write_text('{"id":"review","version":"1","content":"核验报价"}')
    request = ReviewTaskRequest(
        objective="比较供应商", vendor_ids=["smoke-vendor"], session_id=row.id, user_id="alice"
    )
    task = create_review_task(seeded, row.project_id, request)
    seeded.commit()
    assert task.request_snapshot["comparison_months"] == 60
    assert task.request_snapshot["skill_snapshots"][0]["version"] == "1"
    assert (
        task.request_snapshot["memory_context"]["recent_messages"][0]["content"] == "只考虑本地部署"
    )
    policy.write_text('{"id":"review","version":"2","content":"核验依赖"}')
    skill_manager(tmp_path).reload()
    memory.set_preferences(seeded, row.project_id, "alice", {"comparison_months": 12})
    seeded.commit()
    # Frozen inputs still use the original preference and policy.
    assert task.request_snapshot["comparison_months"] == 60
    assert task.request_snapshot["skill_snapshots"][0]["version"] == "1"
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)
    assert run_review_task(task.id, factory, workflow_factory)
    seeded.expire_all()
    episodes = list(seeded.scalars(select(EpisodicMemory).where(EpisodicMemory.task_id == task.id)))
    assert len(episodes) == 1 and episodes[0].source_versions
    assert seeded.get(ResearchTask, task.id).status == "completed"
    assert (
        list(
            seeded.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.task_id == task.id)
                .order_by(ConversationMessage.sequence)
            )
        )[-1].role
        == "assistant"
    )

    seeded.add(ReviewConfig(project_id=row.project_id, comparison_months=24, currency="USD"))
    seeded.commit()
    assert (
        create_review_task(seeded, row.project_id, request).request_snapshot["comparison_months"]
        == 24
    )
    explicit = request.model_copy(update={"comparison_months": 36, "currency": "CNY"})
    assert (
        create_review_task(seeded, row.project_id, explicit).request_snapshot["currency"] == "CNY"
    )
