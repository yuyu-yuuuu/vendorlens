from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from vendorlens.api import app, get_review_enqueuer, stream_task_events
from vendorlens.coordinator import VendorEvidencePackage
from vendorlens.db import get_session
from vendorlens.model_smoke import build_smoke_input
from vendorlens.models import (
    AnalysisReport,
    DomainAssessmentRecord,
    Project,
    Requirement,
    ResearchTask,
    TraceSpan,
    Vendor,
)
from vendorlens.schemas import DomainAssessmentStatus, ReviewDomain, VendorAssessment
from vendorlens.tasks import ReviewTaskRequest, create_review_task, run_review_task
from vendorlens.workflow import DeterministicDomainFallbackRunner, ReviewWorkflow


@pytest.fixture
def seeded(session):
    session.add(Project(id="smoke-project", name="测试项目", description="合成资料"))
    session.flush()
    session.add(Vendor(id="smoke-vendor", project_id="smoke-project", name="测试供应商"))
    session.add(
        Requirement(
            id="requirement-private-deployment",
            project_id="smoke-project",
            source_chunk_id="seed",
            code="R1",
            text="支持私有化部署",
            mandatory=True,
        )
    )
    session.commit()
    return session


def request():
    return ReviewTaskRequest(objective="选择供应商", vendor_ids=["smoke-vendor"])


@contextmanager
def workflow_factory(_session_factory):
    sample = build_smoke_input()

    class Retriever:
        def retrieve_vendor_evidence(self, **kwargs):
            return VendorEvidencePackage(
                project_id=kwargs["project_id"],
                vendor_id=kwargs["vendor_id"],
                document_version_ids=tuple(sample.document_version_ids),
                evidence_by_domain={
                    domain: [
                        item.model_copy(update={"project_id": kwargs["project_id"]})
                        for item in items
                    ]
                    for domain, items in sample.evidence_by_domain.items()
                },
            )

    class Executor:
        def execute(self, input_data):
            fallback = DeterministicDomainFallbackRunner()
            return VendorAssessment(
                vendor_id=input_data.vendor_id,
                domains={
                    domain: fallback.run(input_data, domain).model_copy(
                        update={"status": DomainAssessmentStatus.COMPLETED}
                    )
                    for domain in ReviewDomain
                },
            )

    yield ReviewWorkflow(Retriever(), Executor())


def test_snapshot_is_frozen_and_vendor_scope_checked(seeded):
    task = create_review_task(seeded, "smoke-project", request())
    seeded.commit()
    requirement = seeded.get(Requirement, "requirement-private-deployment")
    requirement.text = "修改后的需求"
    seeded.commit()
    assert task.request_snapshot["requirements"][0]["text"] == "支持私有化部署"
    assert "选择供应商" in task.request_snapshot["project_context"]
    with pytest.raises(ValueError):
        create_review_task(
            seeded, "smoke-project", ReviewTaskRequest(objective="评审", vendor_ids=["other"])
        )
    with pytest.raises(LookupError):
        create_review_task(seeded, "missing", request())


def test_worker_persists_report_events_trace_and_claims_once(seeded):
    task = create_review_task(seeded, "smoke-project", request())
    seeded.commit()
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)
    assert run_review_task(task.id, factory, workflow_factory)
    assert not run_review_task(task.id, factory, workflow_factory)
    seeded.expire_all()
    assert seeded.get(ResearchTask, task.id).status == "completed"
    assert seeded.scalar(select(func.count()).select_from(AnalysisReport)) == 1
    assert seeded.scalar(select(func.count()).select_from(DomainAssessmentRecord)) == 3
    trace = seeded.scalar(select(TraceSpan))
    assert trace.status == "completed" and trace.duration_ms >= 0
    messages = list(stream_task_events(factory, task.id, 1))
    assert messages[0].startswith("id: 2\nevent: run.started")
    assert "node.completed" in "".join(messages)
    assert "run.finished" in messages[-1]
    spans = list(seeded.scalars(select(TraceSpan)))
    assert len([s for s in spans if s.kind == "node"]) == 6
    assert len([s for s in spans if s.kind == "agent"]) == 1
    assert len([s for s in spans if s.kind == "retrieval"]) == 1


def test_worker_failure_is_sanitized(seeded):
    task = create_review_task(seeded, "smoke-project", request())
    seeded.commit()
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)

    @contextmanager
    def broken(_factory):
        raise ValueError("secret-token-do-not-persist")
        yield

    with pytest.raises(RuntimeError, match="review execution failed"):
        run_review_task(task.id, factory, broken)
    seeded.expire_all()
    assert seeded.get(ResearchTask, task.id).status == "failed"
    assert seeded.scalar(select(TraceSpan)).error == "ValueError"
    messages = "".join(stream_task_events(factory, task.id, 0))
    assert "run.failed" in messages and "secret-token" not in messages
    assert seeded.scalar(select(AnalysisReport)) is None


@pytest.fixture
def client(seeded):
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)

    def sessions():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_review_enqueuer] = lambda: lambda *_: None
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_api_submit_read_report_and_resume_sse(client, seeded):
    response = client.post("/projects/smoke-project/tasks", json=request().model_dump(mode="json"))
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert response.json()["status"] == "queued"
    assert client.get(f"/tasks/{task_id}/report").status_code == 409
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)
    run_review_task(task_id, factory, workflow_factory)
    assert client.get(f"/tasks/{task_id}").json()["status"] == "completed"
    assert client.get(f"/tasks/{task_id}/report").json()["report"]
    response = client.get(f"/tasks/{task_id}/events", headers={"Last-Event-ID": "2"})
    assert response.status_code == 200
    assert "id: 1\n" not in response.text and "id: 2\n" not in response.text
    assert "run.finished" in response.text
    assert (
        client.get(f"/tasks/{task_id}/events", headers={"Last-Event-ID": "-1"}).status_code == 422
    )
    assert client.get("/tasks/missing/events").status_code == 404
    assert client.get("/tasks/missing").status_code == 404
    assert client.get("/tasks/missing/report").status_code == 404


def test_api_queue_failure_is_visible(client, seeded):
    def broken(*_):
        raise ConnectionError("secret")

    app.dependency_overrides[get_review_enqueuer] = lambda: broken
    response = client.post("/projects/smoke-project/tasks", json=request().model_dump(mode="json"))
    assert response.status_code == 503
    task_id = response.json()["detail"]["task_id"]
    assert client.get(f"/tasks/{task_id}").json()["status"] == "enqueue_failed"
    assert "secret" not in response.text


def test_api_rejects_bad_requests(client):
    payload = request().model_dump(mode="json")
    assert client.post("/projects/missing/tasks", json=payload).status_code == 404
    assert (
        client.post(
            "/projects/smoke-project/tasks", json={**payload, "vendor_ids": ["other"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/projects/smoke-project/tasks", json={**payload, "vendor_ids": ["smoke-vendor"] * 2}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/projects/smoke-project/tasks", json={**payload, "queries_by_domain": {}}
        ).status_code
        == 422
    )


def test_stream_queued_task_emits_heartbeat(seeded):
    task = create_review_task(seeded, "smoke-project", request())
    seeded.commit()
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)
    stream = stream_task_events(factory, task.id, 0)
    assert "run.queued" in next(stream)
    assert next(stream) == ": keep-alive\n\n"
    stream.close()


def test_enqueue_uses_stable_job_id_and_importable_worker_target(monkeypatch):
    import rq
    from redis import Redis

    from vendorlens.tasks import enqueue_review

    calls = []

    class Queue:
        def __init__(self, name, connection):
            assert name == "vendorlens" and connection == "connection"

        def enqueue(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(Redis, "from_url", lambda *_: "connection")
    monkeypatch.setattr(rq, "Queue", Queue)
    enqueue_review("task-1", "run-1")
    assert calls == [
        (
            ("vendorlens.tasks.execute_review_job", "task-1"),
            {"job_id": "run-1", "job_timeout": 3600},
        )
    ]


def test_enqueue_error_does_not_overwrite_claimed_task(client, seeded):
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)

    def accepted_then_error(task_id, _run_id):
        run_review_task(task_id, factory, workflow_factory)
        raise ConnectionError("lost acknowledgement")

    app.dependency_overrides[get_review_enqueuer] = lambda: accepted_then_error
    response = client.post("/projects/smoke-project/tasks", json=request().model_dump(mode="json"))
    assert response.status_code == 202
    assert response.json()["status"] == "completed"


def test_empty_requirement_catalog_is_rejected(seeded):
    seeded.delete(seeded.get(Requirement, "requirement-private-deployment"))
    seeded.commit()
    with pytest.raises(ValueError, match="requirement catalog"):
        create_review_task(seeded, "smoke-project", request())


def test_retries_and_fallbacks_are_traced(seeded):
    task = create_review_task(seeded, "smoke-project", request())
    seeded.commit()

    class BrokenExecutor:
        def execute(self, _input):
            raise RuntimeError("synthetic failure")

    @contextmanager
    def broken_factory(factory):
        with workflow_factory(factory) as workflow:
            workflow.executor = BrokenExecutor()
            yield workflow

    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)
    assert run_review_task(task.id, factory, broken_factory)
    seeded.expire_all()
    assert seeded.get(ResearchTask, task.id).status == "partially_completed"
    spans = list(seeded.scalars(select(TraceSpan)))
    agents = [s for s in spans if s.kind == "agent"]
    assert [s.attributes["revision_count"] for s in agents] == [0, 1, 2]
    assert all(s.status == "failed" and s.error == "RuntimeError" for s in agents)
    fallbacks = [s for s in spans if s.kind == "fallback"]
    assert len(fallbacks) == 3 and all(s.status == "degraded" for s in fallbacks)
