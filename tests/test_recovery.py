from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_tasks import request, workflow_factory
from test_tasks import seeded as seeded

from vendorlens.checkpoint import sqlite_checkpointer
from vendorlens.models import AnalysisReport, Project, ResearchTask, TraceSpan, utcnow
from vendorlens.recovery import TaskLeaseLost, own_task, prepare_recovery, recover_expired
from vendorlens.tasks import run_review_task


def test_active_lease_cannot_be_stolen_and_expired_lease_is_fenced(session):
    session.add(Project(id="p", name="P"))
    session.flush()
    task = ResearchTask(
        project_id="p",
        objective="review",
        status="running",
        lease_owner="old",
        lease_expires_at=utcnow() + timedelta(seconds=300),
    )
    session.add(task)
    session.commit()
    with pytest.raises(ValueError, match="still active"):
        prepare_recovery(session, task.id)
    task.lease_expires_at = utcnow() - timedelta(seconds=1)
    session.add(TraceSpan(trace_id=task.trace_id, name="unfinished", kind="model"))
    session.commit()
    assert prepare_recovery(session, task.id).status == "queued"
    session.commit()
    assert session.scalar(select(TraceSpan)).status == "interrupted"
    with pytest.raises(TaskLeaseLost):
        own_task(session, task.id, "old")


def test_recovery_resumes_completed_checkpoint_without_replaying_agent(seeded, tmp_path):
    from vendorlens.tasks import create_review_task

    task = create_review_task(seeded, "smoke-project", request())
    seeded.commit()
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)
    path = tmp_path / "checkpoints.sqlite"

    @contextmanager
    def persistent_factory(factory):
        with sqlite_checkpointer(path) as saver, workflow_factory(factory) as workflow:
            workflow.graph = workflow._build().compile(checkpointer=saver)
            yield workflow

    # Simulate death after the graph completes but before the report transaction commits.
    state = dict(task.request_snapshot, task_id=task.id)
    from vendorlens.schemas import RequirementItem, ReviewDomain, ReviewWeights

    state["requirements"] = [RequirementItem.model_validate(r) for r in state["requirements"]]
    state["queries_by_domain"] = {ReviewDomain(k): v for k, v in state["queries_by_domain"].items()}
    state["weights"] = ReviewWeights.model_validate(state["weights"])
    with persistent_factory(factory) as workflow:
        workflow.graph.invoke(state, {"configurable": {"thread_id": task.run_id}})
    task.status = "running"
    task.lease_owner = "dead"
    task.lease_expires_at = utcnow() - timedelta(seconds=1)
    seeded.commit()
    prepare_recovery(seeded, task.id)
    seeded.commit()

    @contextmanager
    def no_replay(factory):
        with persistent_factory(factory) as workflow:

            class Forbidden:
                def execute(self, _):
                    raise AssertionError("completed checkpoint must not replay")

            workflow.executor = Forbidden()
            workflow.graph = workflow._build().compile(checkpointer=workflow.graph.checkpointer)
            yield workflow

    assert run_review_task(task.id, factory, no_replay)
    seeded.expire_all()
    assert seeded.get(ResearchTask, task.id).status == "completed"
    assert seeded.scalar(select(AnalysisReport)) is not None
    with pytest.raises(ValueError, match="report"):
        prepare_recovery(seeded, task.id)


def test_recovery_scanner_queues_only_expired_running_tasks(session):
    session.add(Project(id="p", name="P"))
    session.flush()
    tasks = [
        ResearchTask(
            project_id="p",
            objective="review",
            status="running",
            lease_owner=str(i),
            lease_expires_at=utcnow() + timedelta(seconds=offset),
        )
        for i, offset in enumerate([-1, 500])
    ]
    session.add_all(tasks)
    session.commit()
    calls = []
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    assert recover_expired(factory, lambda *args: calls.append(args)) == 1
    assert calls[0][0] == tasks[0].id
    assert recover_expired(factory, lambda *args: calls.append(args)) == 0


def test_heartbeat_renews_only_the_current_execution_owner(session):
    from vendorlens.recovery import LeaseHeartbeat

    session.add(Project(id="p", name="P"))
    session.flush()
    task = ResearchTask(
        project_id="p",
        objective="review",
        status="running",
        lease_owner="owner",
        lease_expires_at=utcnow() - timedelta(seconds=1),
    )
    session.add(task)
    session.commit()

    class OneIteration:
        def __init__(self):
            self.calls = 0

        def wait(self, _):
            self.calls += 1
            return self.calls > 1

    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    heartbeat = LeaseHeartbeat(factory, task.id, "owner", 300)
    heartbeat.stop = OneIteration()
    heartbeat._run()
    session.refresh(task)
    renewed = task.lease_expires_at
    assert renewed.replace(tzinfo=utcnow().tzinfo) > utcnow()
    task.lease_owner = "replacement"
    session.commit()
    heartbeat.stop = OneIteration()
    heartbeat._run()
    session.refresh(task)
    assert task.lease_expires_at == renewed


def test_recovery_queue_failure_is_persisted_without_revealing_exception(session):
    from vendorlens.models import RunEvent

    session.add(Project(id="p", name="P"))
    session.flush()
    task = ResearchTask(
        project_id="p",
        objective="review",
        status="running",
        lease_owner="dead",
        lease_expires_at=utcnow() - timedelta(seconds=1),
    )
    session.add(task)
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

    def broken(*_):
        raise ConnectionError("secret-queue-password")

    assert recover_expired(factory, broken) == 0
    session.refresh(task)
    assert task.status == "enqueue_failed"
    events = list(session.scalars(select(RunEvent)))
    assert "secret" not in str([event.payload for event in events])


def test_api_retry_rejects_active_execution_and_preserves_snapshot(seeded):
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from vendorlens.api import app, get_review_enqueuer
    from vendorlens.db import get_session
    from vendorlens.tasks import create_review_task

    task = create_review_task(seeded, "smoke-project", request())
    task.status = "running"
    task.lease_owner = "current"
    task.lease_expires_at = utcnow() + timedelta(seconds=300)
    seeded.commit()
    snapshot = dict(task.request_snapshot)
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)

    def sessions():
        with factory() as db:
            yield db

    calls = []
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_review_enqueuer] = lambda: lambda *args: calls.append(args)
    try:
        client = TestClient(app)
        assert client.post(f"/tasks/{task.id}/retry").status_code == 409
        task.lease_expires_at = utcnow() - timedelta(seconds=1)
        seeded.commit()
        response = client.post(f"/tasks/{task.id}/retry")
        assert response.status_code == 202
        assert response.json()["run_id"] == task.run_id
        assert calls[0][0] == task.id
        seeded.refresh(task)
        assert task.request_snapshot == snapshot and task.resume_requested
        assert client.post("/tasks/missing/retry").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_old_worker_cannot_write_checkpoint_after_recovery(session):
    from langgraph.checkpoint.memory import InMemorySaver

    from vendorlens.checkpoint import FencedCheckpointSaver

    session.add(Project(id="p", name="P"))
    session.flush()
    task = ResearchTask(
        project_id="p",
        objective="review",
        status="running",
        lease_owner="dead",
        lease_expires_at=utcnow() - timedelta(seconds=1),
    )
    session.add(task)
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    saver = FencedCheckpointSaver(InMemorySaver(), factory, task.id, "dead")
    prepare_recovery(session, task.id)
    session.commit()
    with pytest.raises(TaskLeaseLost):
        saver.put_writes({"configurable": {"thread_id": task.run_id}}, [], "node")
    with pytest.raises(TaskLeaseLost):
        saver.put({}, {}, {}, {})


def test_recovery_resumes_mid_graph_without_retrieving_again(seeded, tmp_path):
    from vendorlens.schemas import RequirementItem, ReviewDomain, ReviewWeights
    from vendorlens.tasks import create_review_task

    task = create_review_task(seeded, "smoke-project", request())
    seeded.commit()
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)
    path = tmp_path / "mid-graph.sqlite"
    state = dict(task.request_snapshot, task_id=task.id)
    state["requirements"] = [RequirementItem.model_validate(r) for r in state["requirements"]]
    state["queries_by_domain"] = {ReviewDomain(k): v for k, v in state["queries_by_domain"].items()}
    state["weights"] = ReviewWeights.model_validate(state["weights"])
    with sqlite_checkpointer(path) as saver, workflow_factory(factory) as workflow:
        graph = workflow._build().compile(checkpointer=saver, interrupt_before=["analyze_vendors"])
        config = {"configurable": {"thread_id": task.run_id}}
        graph.invoke(state, config)
        assert graph.get_state(config).next == ("analyze_vendors",)
    task.status = "running"
    task.lease_owner = "dead"
    task.lease_expires_at = utcnow() - timedelta(seconds=1)
    seeded.commit()
    prepare_recovery(seeded, task.id)
    seeded.commit()

    @contextmanager
    def resumed(factory):
        with sqlite_checkpointer(path) as saver, workflow_factory(factory) as workflow:

            class Forbidden:
                def retrieve_vendor_evidence(self, **_):
                    raise AssertionError("completed retrieval must not replay")

            workflow.retriever = Forbidden()
            workflow.graph = workflow._build().compile(checkpointer=saver)
            yield workflow

    assert run_review_task(task.id, factory, resumed)
    seeded.expire_all()
    assert seeded.get(ResearchTask, task.id).status == "completed"
    assert seeded.scalar(select(AnalysisReport)) is not None
