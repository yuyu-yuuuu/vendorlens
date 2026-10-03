import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_model_analyzer import SequenceModel, final_draft, review_input

from vendorlens.api import app
from vendorlens.db import get_session
from vendorlens.model_adapter import ModelResponse, ModelToolCall, ModelUsage
from vendorlens.model_analyzer import VendorReviewModelAnalyzer
from vendorlens.models import Project, ResearchTask, TraceSpan
from vendorlens.review_agent import ReviewToolAccessError
from vendorlens.tracing import TraceRecorder, get_active_trace


@pytest.fixture
def recorder(session):
    project = Project(name="Trace测试")
    session.add(project)
    session.flush()
    task = ResearchTask(project_id=project.id, objective="合成资料")
    session.add(task)
    session.flush()
    root = TraceSpan(trace_id=task.trace_id, name="vendor_review", kind="workflow")
    session.add(root)
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    return TraceRecorder(factory, task.trace_id, root.id), task


def test_nested_spans_and_sanitized_failure(session, recorder):
    trace, _ = recorder
    with (
        trace.activate(),
        trace.span("node", "node", prompt="secret", vendor_id="vendor-a") as node,
    ):
        with pytest.raises(ValueError):
            with trace.span("tool", "tool", arguments={"api_key": "secret"}):
                raise ValueError("Authorization: secret")
        with trace.span("sibling", "model") as sibling:
            sibling.attributes["raw_response"] = "secret"
    assert get_active_trace() is None
    rows = list(session.scalars(select(TraceSpan)))
    tool = next(row for row in rows if row.name == "tool")
    assert tool.parent_span_id == node.id
    assert tool.status == "failed" and tool.error == "ValueError"
    assert tool.ended_at is not None and tool.duration_ms >= 0
    assert next(row for row in rows if row.name == "sibling").parent_span_id == node.id
    assert "secret" not in json.dumps([row.attributes for row in rows])


def test_parallel_contexts_do_not_mix_vendor_parents(session, recorder):
    trace, _ = recorder
    with trace.activate(), trace.span("analyze_vendors", "node") as node:

        def run(vendor):
            with trace.span(vendor, "agent", vendor_id=vendor) as agent:
                with trace.span("chat_completion", "model") as model:
                    return agent.id, model.id

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(copy_context().run, run, vendor) for vendor in ("a", "b")]
            ids = [future.result() for future in futures]
    for agent_id, model_id in ids:
        assert session.get(TraceSpan, agent_id).parent_span_id == node.id
        assert session.get(TraceSpan, model_id).parent_span_id == agent_id


def test_model_and_tool_spans_capture_usage_not_payloads(session, recorder):
    trace, _ = recorder
    model = SequenceModel(
        [
            ModelResponse(
                model="fake-model",
                usage_reported=True,
                usage=ModelUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
                tool_calls=[
                    ModelToolCall(
                        id="call-1",
                        name="calculate_tco",
                        arguments={
                            "one_time_cost": 800000,
                            "annual_recurring_cost": 100000,
                            "optional_cost": 50000,
                            "evidence_ids": ["cost-ev"],
                        },
                    )
                ],
            ),
            ModelResponse(content=json.dumps(final_draft())),
        ]
    )
    with trace.activate(), trace.span("VendorReviewAgent", "agent") as agent:
        result = VendorReviewModelAnalyzer(model)(review_input(), None)
    assert result.cost_assessment.total_cost_of_ownership == 1100000
    rows = list(session.scalars(select(TraceSpan)))
    models = [row for row in rows if row.kind == "model"]
    assert len(models) == 2
    assert all(row.parent_span_id == agent.id for row in models)
    assert models[0].attributes["total_tokens"] == 120
    assert models[1].attributes["usage_reported"] is False
    assert "total_tokens" not in models[1].attributes
    tool = next(row for row in rows if row.kind == "tool")
    assert tool.name == "calculate_tco" and tool.parent_span_id == agent.id
    assert tool.attributes["argument_count"] == 4
    assert "cost-ev" not in json.dumps([row.attributes for row in rows])


def test_unregistered_tool_name_and_error_payload_are_not_saved(session, recorder):
    trace, _ = recorder
    model = SequenceModel(
        [
            ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        id="call-1",
                        name="secret-unregistered-name",
                        arguments={"api_key": "secret"},
                    )
                ]
            )
        ]
    )
    with trace.activate(), pytest.raises(ReviewToolAccessError):
        VendorReviewModelAnalyzer(model)(review_input(), None)
    tool = session.scalar(select(TraceSpan).where(TraceSpan.kind == "tool"))
    assert tool.name == "unregistered_tool" and tool.status == "failed"
    assert tool.error == "ReviewToolAccessError"


def test_trace_api_reports_partial_token_usage(session, recorder):
    trace, task = recorder
    with trace.span("chat_completion", "model", usage_reported=True, total_tokens=120):
        pass
    with trace.span("chat_completion", "model", usage_reported=False):
        pass

    factory = sessionmaker(bind=session.get_bind())

    def sessions():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = sessions
    try:
        response = TestClient(app).get(f"/tasks/{task.id}/trace")
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["model_calls"] == 2
        assert payload["summary"]["total_tokens"] == 120
        assert payload["summary"]["usage_reported_calls"] == 1
        assert payload["summary"]["usage_complete"] is False
        assert len(payload["spans"]) == 3
        assert TestClient(app).get("/tasks/missing/trace").status_code == 404
    finally:
        app.dependency_overrides.clear()
