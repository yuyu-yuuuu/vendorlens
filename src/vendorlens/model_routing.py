"""Health-feedback routing per vendor attempt, with durable cooldown and probe leases."""

import hashlib
import json
import math
from contextlib import nullcontext
from datetime import UTC, timedelta
from time import monotonic

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from vendorlens.config import ModelEndpoint, get_settings
from vendorlens.model_adapter import ModelConfigurationError, ModelError
from vendorlens.models import RoutingAttempt, RoutingHealth, new_id, utcnow
from vendorlens.observability import estimated_cost
from vendorlens.schemas import DomainAssessmentStatus
from vendorlens.tracing import get_active_trace


class RoutingUnavailable(ModelError):
    pass


def aware(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def configuration_hash(endpoint):
    # Health belongs to the endpoint configuration, not just its display label.
    payload = endpoint.model_dump(mode="json", exclude={"api_key"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def configured_endpoints(settings):
    if settings.model_endpoints:
        return settings.model_endpoints
    if not settings.model_base_url or not settings.model_api_key or not settings.model_name:
        raise ModelConfigurationError("complete model configuration is required")
    return [
        ModelEndpoint(
            id="default",
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            model=settings.model_name,
            provider=settings.model_provider,
            timeout_seconds=settings.model_timeout_seconds,
            input_price_per_million=settings.model_input_price_per_million,
            output_price_per_million=settings.model_output_price_per_million,
            price_currency=settings.model_price_currency,
        )
    ]


class HealthRouter:
    def __init__(self, session_factory, settings=None, *, clock=utcnow):
        self.factory = session_factory
        self.settings = settings or get_settings()
        self.endpoints = configured_endpoints(self.settings)
        self.clock = clock

    def _samples(self, db, endpoint, now):
        rows = list(
            db.scalars(
                select(RoutingAttempt)
                .where(
                    RoutingAttempt.config_hash == configuration_hash(endpoint),
                    RoutingAttempt.created_at
                    >= now - timedelta(seconds=self.settings.routing_window_seconds),
                    RoutingAttempt.status != "running",
                )
                .order_by(RoutingAttempt.created_at.desc())
                .limit(200)
            )
        )
        for index, row in enumerate(rows):
            if row.decision.get("recovered"):
                return rows[: index + 1]
        return rows

    def _score(self, endpoint, rows):
        count = len(rows)
        errors = sum(r.status == "error" for r in rows) / count if count else 0
        rate_limits = (
            sum(r.error_type == "ModelRateLimitError" for r in rows) / count if count else 0
        )
        quality = sum(r.quality_failure_rate for r in rows) / count if count else 0
        latencies = sorted(ms for r in rows for ms in r.request_latencies)
        p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else 0
        # Costs of different currencies are never compared without an FX policy.
        priced = [
            r.estimated_cost
            for r in rows
            if r.estimated_cost is not None and r.price_currency == endpoint.price_currency
        ]
        comparable = len({e.price_currency for e in self.endpoints}) == 1
        cost_penalty = min(sum(priced) / len(priced) * 10, 10) if priced and comparable else 0
        penalty = 60 * errors + 20 * rate_limits + 40 * quality
        penalty += min(p95 / self.settings.routing_latency_target_ms * 10, 30) + cost_penalty
        # Cold starts keep base score; small samples do not cause health downweighting.
        score = (
            endpoint.base_score - penalty
            if count >= self.settings.routing_min_samples
            else (endpoint.base_score)
        )
        return {
            "endpoint_id": endpoint.id,
            "score": round(score, 4),
            "samples": count,
            "error_rate": errors,
            "rate_limit_rate": rate_limits,
            "quality_failure_rate": quality,
            "p95_request_ms": p95,
            "cost_penalty": cost_penalty if comparable else None,
        }

    def choose(self, task_id, vendor_id):
        now = self.clock()
        attempt_id = new_id()
        with self.factory() as db:
            candidates = []
            for endpoint in self.endpoints:
                key = configuration_hash(endpoint)
                health = db.get(RoutingHealth, key)
                if health is None:
                    try:
                        with db.begin_nested():
                            db.add(RoutingHealth(endpoint_id=key))
                            db.flush()
                    except IntegrityError:
                        pass
                    health = db.get(RoutingHealth, key)
                stats = self._score(endpoint, self._samples(db, endpoint, now))
                cooling = health.cooldown_until and aware(health.cooldown_until) > now
                probing = health.probe_until and aware(health.probe_until) > now
                stats["available"] = not bool(cooling or probing)
                stats["probe"] = bool(health.cooldown_until and not cooling)
                candidates.append((endpoint, stats))
            ordered = sorted(
                candidates,
                key=lambda pair: (
                    # An expired cooldown gets one probe so a degraded endpoint can recover.
                    not pair[1]["probe"],
                    -pair[1]["score"],
                    pair[0].id,
                ),
            )
            for endpoint, stats in ordered:
                if not stats["available"]:
                    continue
                if stats["probe"]:
                    changed = db.execute(
                        update(RoutingHealth).execution_options(synchronize_session=False)
                        .where(
                            RoutingHealth.endpoint_id == configuration_hash(endpoint),
                            RoutingHealth.cooldown_until <= now,
                            or_(
                                RoutingHealth.probe_until.is_(None),
                                RoutingHealth.probe_until <= now,
                            ),
                        )
                        .values(
                            probe_owner=attempt_id,
                            probe_until=now
                            + timedelta(seconds=self.settings.routing_probe_seconds),
                        )
                    )
                    if not changed.rowcount:
                        continue
                decision = {
                    "candidates": [s for _, s in candidates],
                    "selected": endpoint.id,
                    "probe": stats["probe"],
                    "single_endpoint": len(self.endpoints) == 1,
                }
                db.add(
                    RoutingAttempt(
                        id=attempt_id,
                        endpoint_id=endpoint.id,
                        config_hash=configuration_hash(endpoint),
                        task_id=task_id,
                        vendor_id=vendor_id,
                        decision=decision,
                        created_at=now,
                    )
                )
                db.commit()
                return endpoint, attempt_id, decision
            raise RoutingUnavailable("all model endpoints are cooling down or being probed")

    def finish(
        self,
        attempt_id,
        *,
        error=None,
        quality=0,
        latency_ms=0,
        requests=None,
        cost=None,
        currency=None,
    ):
        now = self.clock()
        with self.factory() as db:
            row = db.get(RoutingAttempt, attempt_id)
            if row is None or row.status != "running":
                return
            row.status = "error" if error else "quality_failed" if quality else "completed"
            row.error_type = type(error).__name__ if error else None
            row.quality_failure_rate = quality
            row.latency_ms = latency_ms
            row.request_latencies = requests or []
            row.estimated_cost, row.price_currency = cost, currency
            db.flush()
            health = db.scalar(
                select(RoutingHealth)
                .where(RoutingHealth.endpoint_id == row.config_hash)
                .with_for_update()
            )
            endpoint = next(e for e in self.endpoints if configuration_hash(e) == row.config_hash)
            samples = self._samples(db, endpoint, now)
            # A healthy probe resets the sampled health window; old errors stop penalizing it.
            if health.probe_owner == attempt_id:
                if not error and not quality:
                    # Keep historical audit rows; mark a recovery boundary in decisions instead.
                    decision = dict(row.decision, recovered=True)
                    row.decision = decision
                    health.cooldown_until = None
                else:
                    health.cooldown_until = now + timedelta(
                        seconds=self.settings.routing_cooldown_seconds
                    )
                health.probe_owner = None
                health.probe_until = None
            elif len(samples) >= self.settings.routing_min_samples:
                stats = self._score(endpoint, samples)
                if stats["error_rate"] >= 0.5 or stats["quality_failure_rate"] >= 0.5:
                    health.cooldown_until = now + timedelta(
                        seconds=self.settings.routing_cooldown_seconds
                    )
            db.commit()

    def summary(self):
        with self.factory() as db:
            now = self.clock()
            result = []
            for endpoint in self.endpoints:
                stats = self._score(endpoint, self._samples(db, endpoint, now))
                health = db.get(RoutingHealth, configuration_hash(endpoint))
                stats["cooldown_until"] = aware(health.cooldown_until) if health else None
                stats["probe_until"] = aware(health.probe_until) if health else None
                result.append(stats)
            return result


class ObservedChatModel:
    def __init__(self, model, endpoint):
        self.model, self.endpoint = model, endpoint
        self.latencies = []
        self.costs = []

    def complete(self, **kwargs):
        started = monotonic()
        try:
            response = self.model.complete(**kwargs)
            priced = (
                estimated_cost(
                    response.usage,
                    self.endpoint.input_price_per_million,
                    self.endpoint.output_price_per_million,
                    self.endpoint.price_currency,
                )
                if response.usage_reported
                else {}
            )
            self.costs.append(priced.get("estimated_cost"))
            return response
        except Exception:
            self.costs.append(None)
            raise
        finally:
            self.latencies.append((monotonic() - started) * 1000)


class RoutedVendorReviewExecutor:
    def __init__(self, factory, settings=None, *, client=None, executor_builder=None):
        from vendorlens.model_analyzer import build_vendor_review_executor

        self.settings = settings or get_settings()
        self.router = HealthRouter(factory, self.settings)
        self.client = client
        self.builder = executor_builder or build_vendor_review_executor

    def execute(self, input_data):
        endpoint, attempt, decision = self.router.choose(input_data.task_id, input_data.vendor_id)
        overrides = dict(
            model_provider=endpoint.provider,
            model_base_url=endpoint.base_url,
            model_api_key=endpoint.api_key,
            model_name=endpoint.model,
            model_timeout_seconds=endpoint.timeout_seconds,
            model_input_price_per_million=endpoint.input_price_per_million,
            model_output_price_per_million=endpoint.output_price_per_million,
            model_price_currency=endpoint.price_currency,
        )
        tracer = get_active_trace()
        scope = (
            tracer.span(
                "model_route",
                "routing",
                endpoint_id=endpoint.id,
                route_attempt_id=attempt,
                probe=decision["probe"],
            )
            if tracer
            else nullcontext()
        )
        started = monotonic()
        observed = None
        error = None
        quality = 0
        try:
            with scope:
                executor = self.builder(
                    self.settings.model_copy(update=overrides), client=self.client
                )
                analyzer = executor.agent._analyzer
                observed = ObservedChatModel(analyzer.chat_model, endpoint)
                analyzer.chat_model = observed
                result = executor.execute(input_data)
                active = input_data.revision_domains or set(result.domains)
                quality = sum(
                    result.domains[d].status is DomainAssessmentStatus.FAILED for d in active
                ) / len(active)
                return result
        except Exception as caught:
            error = caught
            raise
        finally:
            costs = observed.costs if observed else []
            self.router.finish(
                attempt,
                error=error,
                quality=quality,
                latency_ms=(monotonic() - started) * 1000,
                requests=observed.latencies if observed else [],
                cost=sum(costs) if costs and all(c is not None for c in costs) else None,
                currency=endpoint.price_currency,
            )
