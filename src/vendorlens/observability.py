"""Durable low-cardinality metrics and explicitly priced token estimates."""

from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import select

from vendorlens.models import ResearchTask, TraceSpan


def estimated_cost(usage, input_price, output_price, currency):
    if input_price is None or output_price is None:
        return {}
    amount = (
        Decimal(usage.prompt_tokens) * Decimal(str(input_price))
        + Decimal(usage.completion_tokens) * Decimal(str(output_price))
    ) / 1000000
    return {
        "estimated_cost": float(amount),
        "price_currency": currency,
        "input_price_per_million": input_price,
        "output_price_per_million": output_price,
    }


def cost_summary(models):
    priced = [span for span in models if "estimated_cost" in span.attributes]
    by_currency = defaultdict(float)
    for span in priced:
        by_currency[span.attributes["price_currency"]] += span.attributes["estimated_cost"]
    return {
        "priced_calls": len(priced),
        "cost_complete": bool(models) and len(priced) == len(models),
        "estimated_costs": dict(by_currency),
    }


def prometheus_metrics(session):
    tasks = Counter(session.scalars(select(ResearchTask.status)))
    spans = list(session.scalars(select(TraceSpan)))
    output = [
        "# HELP vendorlens_tasks Current persisted tasks by status.",
        "# TYPE vendorlens_tasks gauge",
    ]
    for status in (
        "queued",
        "running",
        "completed",
        "partially_completed",
        "failed",
        "enqueue_failed",
    ):
        output.append(f'vendorlens_tasks{{status="{status}"}} {tasks[status]}')
    output.extend(
        [
            "# HELP vendorlens_spans Recorded execution spans by kind.",
            "# TYPE vendorlens_spans counter",
        ]
    )
    for kind in ("workflow", "node", "retrieval", "agent", "model", "tool", "fallback", "routing"):
        subset = [span for span in spans if span.kind == kind]
        output.append(f'vendorlens_spans_total{{kind="{kind}"}} {len(subset)}')
    output.extend(
        [
            "# HELP vendorlens_model_tokens Reported model tokens.",
            "# TYPE vendorlens_model_tokens counter",
        ]
    )
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = sum(
            span.attributes.get(key, 0)
            for span in spans
            if span.kind == "model" and span.attributes.get("usage_reported")
        )
        output.append(f'vendorlens_model_tokens_total{{type="{key}"}} {value}')
    durations = [
        span.duration_ms / 1000
        for span in spans
        if span.kind == "model" and span.duration_ms is not None
    ]
    output.extend(
        [
            "# HELP vendorlens_model_duration_seconds Recorded model request duration.",
            "# TYPE vendorlens_model_duration_seconds histogram",
        ]
    )
    for bound in (0.1, 0.5, 1, 5, 10, 30, 60):
        count = sum(value <= bound for value in durations)
        output.append(f'vendorlens_model_duration_seconds_bucket{{le="{bound}"}} {count}')
    output.append(f'vendorlens_model_duration_seconds_bucket{{le="+Inf"}} {len(durations)}')
    output.append(f"vendorlens_model_duration_seconds_count {len(durations)}")
    output.append(f"vendorlens_model_duration_seconds_sum {sum(durations)}")
    return "\n".join(output) + "\n"
