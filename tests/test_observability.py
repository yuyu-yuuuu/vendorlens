from types import SimpleNamespace

from vendorlens.model_adapter import ModelUsage
from vendorlens.models import Project, ResearchTask, TraceSpan
from vendorlens.observability import cost_summary, estimated_cost, prometheus_metrics


def test_explicit_prices_produce_estimates_and_unknown_never_means_zero():
    usage = ModelUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
    assert estimated_cost(usage, None, 8, "USD") == {}
    priced = estimated_cost(usage, 2, 8, "USD")
    assert priced["estimated_cost"] == 0.00012
    summary = cost_summary([SimpleNamespace(attributes=priced), SimpleNamespace(attributes={})])
    assert summary["cost_complete"] is False
    assert summary["estimated_costs"] == {"USD": 0.00012}
    zero = estimated_cost(usage, 0, 0, "CNY")
    assert cost_summary([SimpleNamespace(attributes=zero)])["cost_complete"] is True


def test_metrics_include_usage_and_duration_but_no_project_or_raw_payload_labels(session):
    session.add(Project(id="secret-project-id", name="Private"))
    session.flush()
    session.add(ResearchTask(project_id="secret-project-id", objective="secret-text"))
    session.add(
        TraceSpan(
            trace_id="private-trace",
            name="chat",
            kind="model",
            duration_ms=500,
            attributes={
                "usage_reported": True,
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
                "prompt": "secret-text",
            },
        )
    )
    session.commit()
    metrics = prometheus_metrics(session)
    assert 'vendorlens_tasks{status="queued"} 1' in metrics
    assert 'vendorlens_model_tokens_total{type="total_tokens"} 30' in metrics
    assert 'vendorlens_model_duration_seconds_bucket{le="0.5"} 1' in metrics
    assert "secret" not in metrics and "private-trace" not in metrics


def test_configured_api_access_key_protects_business_routes_without_leaking_key(monkeypatch):
    from fastapi.testclient import TestClient

    import vendorlens.auth as auth
    from vendorlens.api import app
    from vendorlens.config import Settings

    monkeypatch.setattr(
        auth, "get_settings", lambda: Settings(_env_file=None, api_access_key="private-test-key")
    )
    client = TestClient(app)
    assert client.get("/tasks/missing").status_code == 401
    invalid = client.get("/tasks/missing", headers={"Authorization": "Bearer wrong"})
    assert invalid.status_code == 401 and "private-test-key" not in invalid.text
    # Use a DB dependency override to prove a valid credential reaches the handler.
    from vendorlens.db import get_session

    class Database:
        def get(self, *args):
            return None

    app.dependency_overrides[get_session] = Database
    try:
        assert (
            client.get(
                "/tasks/missing", headers={"Authorization": "Bearer private-test-key"}
            ).status_code
            == 404
        )
    finally:
        app.dependency_overrides.clear()
