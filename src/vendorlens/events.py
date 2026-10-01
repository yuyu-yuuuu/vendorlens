from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vendorlens.models import ResearchTask, RunEvent


class RunEventStore:
    def append(self, session: Session, task_id: str, event_type: str, payload: dict) -> RunEvent:
        task = session.scalar(
            select(ResearchTask).where(ResearchTask.id == task_id).with_for_update()
        )
        if task is None:
            raise ValueError("research task does not exist")
        sequence = (
            session.scalar(select(func.max(RunEvent.sequence)).where(RunEvent.task_id == task_id))
            or 0
        ) + 1
        event = RunEvent(task_id=task_id, sequence=sequence, event_type=event_type, payload=payload)
        session.add(event)
        session.flush()
        return event

    def after(self, session: Session, task_id: str, sequence: int) -> list[RunEvent]:
        return list(
            session.scalars(
                select(RunEvent)
                .where(RunEvent.task_id == task_id, RunEvent.sequence > sequence)
                .order_by(RunEvent.sequence)
            )
        )


def format_sse(event: RunEvent) -> str:
    data = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"
