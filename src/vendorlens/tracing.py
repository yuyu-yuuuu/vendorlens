from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic

from sqlalchemy.orm import Session

from vendorlens.models import TraceSpan, utcnow

_active_recorder: ContextVar[TraceRecorder | None] = ContextVar("trace_recorder", default=None)
ALLOWED_ATTRIBUTES = {
    "endpoint_id",
    "route_attempt_id",
    "probe",
    "vendor_id",
    "revision_count",
    "evidence_count",
    "version_count",
    "domain_count",
    "failed_domain_count",
    "degraded_domain_count",
    "state_status",
    "model",
    "round",
    "tool_call_count",
    "argument_count",
    "usage_reported",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "domain",
    "estimated_cost",
    "price_currency",
    "input_price_per_million",
    "output_price_per_million",
}


def safe_attributes(attributes: dict) -> dict:
    """Only selected scalar metadata; never serialize prompts, tool arguments or outputs."""
    return {
        key: value[:200] if isinstance(value, str) else value
        for key, value in attributes.items()
        if key in ALLOWED_ATTRIBUTES and isinstance(value, (str, int, float, bool))
    }


@dataclass
class SpanHandle:
    id: str
    attributes: dict = field(default_factory=dict)
    status: str = "completed"


class TraceRecorder:
    def __init__(self, session_factory: Callable[[], Session], trace_id: str, root_span_id: str):
        self.session_factory = session_factory
        self.trace_id = trace_id
        self._parent: ContextVar[str] = ContextVar(f"trace_parent_{trace_id}", default=root_span_id)

    @contextmanager
    def activate(self) -> Iterator[TraceRecorder]:
        token = _active_recorder.set(self)
        try:
            yield self
        finally:
            _active_recorder.reset(token)

    @contextmanager
    def span(self, name: str, kind: str, **attributes) -> Iterator[SpanHandle]:
        started = monotonic()
        with self.session_factory() as session:
            row = TraceSpan(
                trace_id=self.trace_id,
                parent_span_id=self._parent.get(),
                name=name,
                kind=kind,
                attributes=safe_attributes(attributes),
            )
            session.add(row)
            session.flush()
            handle = SpanHandle(row.id, dict(row.attributes))
            session.commit()
        token = self._parent.set(handle.id)
        error_type = None
        try:
            yield handle
        except Exception as error:
            handle.status = "failed"
            error_type = type(error).__name__
            raise
        finally:
            self._parent.reset(token)
            with self.session_factory() as session:
                row = session.get(TraceSpan, handle.id)
                if row.status == "running":
                    row.status = handle.status
                    row.attributes = safe_attributes(handle.attributes)
                    row.error = error_type
                    row.ended_at = utcnow()
                    row.duration_ms = int((monotonic() - started) * 1000)
                    session.commit()


def get_active_trace() -> TraceRecorder | None:
    return _active_recorder.get()
