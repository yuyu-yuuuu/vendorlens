"""Database leases and explicit recovery; no active execution may be stolen."""

from __future__ import annotations

from datetime import UTC, timedelta
from threading import Event, Thread

from sqlalchemy import select, update

from vendorlens.events import RunEventStore
from vendorlens.models import AnalysisReport, ResearchTask, TraceSpan, new_id, utcnow


class TaskLeaseLost(RuntimeError):
    pass


def own_task(session, task_id, owner):
    changed = session.execute(
        update(ResearchTask)
        .where(
            ResearchTask.id == task_id,
            ResearchTask.status == "running",
            ResearchTask.lease_owner == owner,
        )
        .values(lease_owner=owner)
    ).rowcount
    if not changed:
        raise TaskLeaseLost("execution no longer owns this task")
    return session.get(ResearchTask, task_id)


class LeaseHeartbeat:
    def __init__(self, factory, task_id, owner, seconds):
        self.factory, self.task_id, self.owner, self.seconds = factory, task_id, owner, seconds
        self.stop = Event()
        self.thread = Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.wait(max(1, self.seconds / 3)):
            try:
                with self.factory() as session:
                    changed = session.execute(
                        update(ResearchTask)
                        .where(
                            ResearchTask.id == self.task_id,
                            ResearchTask.status == "running",
                            ResearchTask.lease_owner == self.owner,
                        )
                        .values(lease_expires_at=utcnow() + timedelta(seconds=self.seconds))
                    ).rowcount
                    session.commit()
                    if not changed:
                        return
            except Exception:
                # A DB outage may expire the lease; fencing checks protect subsequent writes.
                continue

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=2)


def prepare_recovery(session, task_id):
    task = session.scalar(
        select(ResearchTask)
        .where(
            ResearchTask.id == task_id,
        )
        .with_for_update()
    )
    if task is None:
        raise LookupError("task not found")
    if session.scalar(select(AnalysisReport.id).where(AnalysisReport.task_id == task_id)):
        raise ValueError("a task with a report cannot be replayed")
    now = utcnow()
    expiry = task.lease_expires_at
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if task.status == "running" and expiry and expiry > now:
        raise ValueError("task execution lease is still active")
    if task.status not in {"running", "failed", "enqueue_failed"}:
        raise ValueError("task is not recoverable")
    old_status, old_owner = task.status, task.lease_owner
    # Compare-and-swap protects SQLite too, where SELECT FOR UPDATE is ineffective.
    changed = session.execute(
        update(ResearchTask)
        .where(
            ResearchTask.id == task_id,
            ResearchTask.status == old_status,
            ResearchTask.lease_owner == old_owner,
            ResearchTask.lease_expires_at == task.lease_expires_at,
        )
        .values(status="queued", lease_owner=None, lease_expires_at=None, resume_requested=True)
    ).rowcount
    if not changed:
        raise ValueError("task changed while preparing recovery")
    session.execute(
        update(TraceSpan)
        .where(
            TraceSpan.trace_id == task.trace_id,
            TraceSpan.status == "running",
        )
        .values(status="interrupted", ended_at=now, error="WorkerInterrupted")
    )
    RunEventStore().append(session, task_id, "run.requeued", {"resume": True})
    session.flush()
    session.refresh(task)
    from vendorlens.outbox import schedule

    schedule(session, task, job_id=f"{task.run_id}:recovery:{new_id()}")
    return task


def expired_tasks(session, limit=100):
    return list(
        session.scalars(
            select(ResearchTask.id)
            .where(
                ResearchTask.status == "running",
                ResearchTask.lease_expires_at < utcnow(),
            )
            .order_by(ResearchTask.lease_expires_at)
            .limit(limit)
        )
    )


def recover_expired(session_factory, enqueuer):
    from vendorlens.outbox import dispatch_one, dispatch_pending

    with session_factory() as session:
        ids = expired_tasks(session)
    recovered = 0
    for task_id in ids:
        with session_factory() as session:
            try:
                task = prepare_recovery(session, task_id)
            except (ValueError, LookupError):
                continue
            session.commit()
            try:
                if not dispatch_one(session_factory, task.id, enqueuer):
                    continue
            except Exception:
                changed = session.execute(
                    update(ResearchTask)
                    .where(
                        ResearchTask.id == task_id,
                        ResearchTask.status == "queued",
                    )
                    .values(status="enqueue_failed")
                ).rowcount
                if changed:
                    RunEventStore().append(
                        session, task_id, "run.enqueue_failed", {"error": "queue_unavailable"}
                    )
                    session.commit()
                    continue
                session.rollback()
            recovered += 1
    dispatch_pending(session_factory, enqueuer)
    return recovered


def main():
    import argparse
    from time import sleep

    from vendorlens.db import SessionLocal
    from vendorlens.tasks import enqueue_review

    parser = argparse.ArgumentParser(description="Recover expired review execution leases")
    parser.add_argument("--watch", action="store_true")
    arguments = parser.parse_args()
    while True:
        try:
            recover_expired(SessionLocal, enqueue_review)
        except Exception:
            print("Recovery unavailable; inspect database and queue connectivity.")
        if not arguments.watch:
            return
        sleep(30)


if __name__ == "__main__":
    main()
