import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_model_analyzer import final_draft, review_input

from vendorlens.config import ModelEndpoint, Settings
from vendorlens.model_adapter import ModelRateLimitError
from vendorlens.model_routing import HealthRouter, RoutedVendorReviewExecutor, RoutingUnavailable
from vendorlens.models import RoutingAttempt, RoutingHealth, utcnow
from vendorlens.schemas import DomainAssessmentStatus, ReviewDomain


def endpoint(name, **changes):
    return ModelEndpoint(
        id=name, base_url="https://model.invalid/v1", api_key="secret-key", model=name, **changes
    )


def settings(*endpoints, **kwargs):
    return Settings(_env_file=None, model_endpoints=list(endpoints), **kwargs)


def test_cooldown_switch_probe_lease_and_successful_recovery(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    now = [utcnow()]
    a, b = endpoint("a"), endpoint("b", base_score=95)
    router = HealthRouter(factory, settings(a), clock=lambda: now[0])
    for _ in range(3):
        _, attempt, decision = router.choose("t", "vendor")
        assert decision["single_endpoint"]
        router.finish(attempt, error=ModelRateLimitError("secret error"), requests=[100])
        now[0] += timedelta(seconds=1)
    with pytest.raises(RoutingUnavailable):
        router.choose("t", "vendor")
    multi = HealthRouter(factory, settings(a, b), clock=lambda: now[0])
    chosen, _, decision = multi.choose("t", "vendor")
    assert chosen.id == "b"
    assert not next(s for s in decision["candidates"] if s["endpoint_id"] == "a")["available"]
    now[0] += timedelta(seconds=61)
    chosen, attempt, decision = multi.choose("t", "vendor")
    assert chosen.id == "a" and decision["probe"]
    # An in-flight recovery probe cannot be simultaneously selected again.
    assert multi.choose("t", "other-vendor")[0].id == "b"
    multi.finish(attempt, requests=[50])
    stats = {s["endpoint_id"]: s for s in multi.summary()}
    assert stats["a"]["error_rate"] == 0
    assert stats["a"]["cooldown_until"] is None
    assert multi.choose("t", "vendor")[0].id == "a"
    rows = list(session.scalars(select(RoutingAttempt)))
    assert "secret" not in json.dumps([r.decision for r in rows])
    assert all(r.error_type in {None, "ModelRateLimitError"} for r in rows)


def test_quality_feedback_downweights_without_waiting_for_transport_failure(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    router = HealthRouter(
        factory, settings(endpoint("a"), endpoint("b", base_score=95), routing_min_samples=1)
    )
    chosen, attempt, _ = router.choose("t", "vendor")
    assert chosen.id == "a"
    router.finish(attempt, quality=1 / 3, requests=[1])
    chosen, _, decision = router.choose("t", "vendor")
    assert chosen.id == "b"
    assert next(s for s in decision["candidates"] if s["endpoint_id"] == "a")["score"] < 95


def test_concurrent_callers_only_one_owns_recovery_probe(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    now = [utcnow()]
    a, b = endpoint("a"), endpoint("b", base_score=95)
    single = HealthRouter(factory, settings(a, routing_min_samples=1), clock=lambda: now[0])
    _, attempt, _ = single.choose("t", "v")
    single.finish(attempt, error=ModelRateLimitError())
    now[0] += timedelta(seconds=61)
    router = HealthRouter(factory, settings(a, b), clock=lambda: now[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        selected = list(pool.map(lambda i: router.choose("t", f"v{i}")[0].id, range(4)))
    assert selected.count("a") == 1
    assert selected.count("b") == 3


def test_failed_probe_reenters_cooldown_and_expired_probe_can_be_reclaimed(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    now = [utcnow()]
    router = HealthRouter(
        factory,
        settings(endpoint("a"), routing_min_samples=1, routing_probe_seconds=10),
        clock=lambda: now[0],
    )
    _, attempt, _ = router.choose("t", "v")
    router.finish(attempt, error=ModelRateLimitError())
    now[0] += timedelta(seconds=61)
    _, probe, _ = router.choose("t", "v")
    now[0] += timedelta(seconds=11)
    _, new_probe, decision = router.choose("t", "v")
    assert new_probe != probe and decision["probe"]
    router.finish(new_probe, quality=1)
    with pytest.raises(RoutingUnavailable):
        router.choose("t", "v")
    assert session.scalar(select(RoutingHealth)).probe_owner is None


def completion(content=None, calls=None):
    return {
        "choices": [{"message": {"content": content, "tool_calls": calls or []}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def test_routed_real_tool_loop_switches_after_rate_limit_and_preserves_cost_gate(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if payload["model"] == "a":
            return httpx.Response(429, json={"error": "private provider response"})
        if payload["tool_choice"] != "auto":
            tool = {
                "id": "cost",
                "type": "function",
                "function": {
                    "name": "calculate_tco",
                    "arguments": json.dumps(
                        {
                            "one_time_cost": 800000,
                            "annual_recurring_cost": 100000,
                            "optional_cost": 50000,
                            "evidence_ids": ["cost-ev"],
                        }
                    ),
                },
            }
            return httpx.Response(200, json=completion(calls=[tool]))
        return httpx.Response(200, json=completion(content=json.dumps(final_draft(total=999))))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        runner = RoutedVendorReviewExecutor(
            factory,
            settings(
                endpoint("a"),
                endpoint("b", base_score=95, input_price_per_million=1, output_price_per_million=2),
                routing_min_samples=1,
            ),
            client=client,
        )
        with pytest.raises(ModelRateLimitError):
            runner.execute(review_input())
        result = runner.execute(review_input())
    cost = result.domains[ReviewDomain.COST_MODELING].cost_assessment
    assert cost.total_cost_of_ownership == 1100000
    assert result.domains[ReviewDomain.COST_MODELING].status is DomainAssessmentStatus.COMPLETED
    attempts = list(session.scalars(select(RoutingAttempt).order_by(RoutingAttempt.created_at)))
    assert [a.endpoint_id for a in attempts] == ["a", "b"]
    assert attempts[0].error_type == "ModelRateLimitError"
    assert len(attempts[1].request_latencies) == 2
    assert attempts[1].estimated_cost == pytest.approx(0.00004)
    assert "secret-key" not in json.dumps([a.decision for a in attempts])
    assert calls[-1]["messages"][-1]["role"] == "tool"
