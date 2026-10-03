"""Transactional task dispatch with bounded retry and an expiring dispatcher lease."""

from datetime import timedelta

from sqlalchemy import or_, select, update

from vendorlens.models import ResearchTask, TaskDispatch, new_id, utcnow


def schedule(session, task, *, job_id=None):
    row = session.get(TaskDispatch, task.id)
    if row is None:
        row = TaskDispatch(task_id=task.id, job_id=job_id or task.run_id)
        session.add(row)
    else:
        row.job_id = job_id or task.run_id
        row.delivered_at = row.lease_owner = row.lease_until = row.error_type = None
        row.next_attempt_at = utcnow()
    session.flush()
    return row


def dispatch_one(factory, task_id, enqueuer):
    owner, now = new_id(), utcnow()
    with factory() as session:
        claimed = session.execute(
            update(TaskDispatch)
            .where(
                TaskDispatch.task_id == task_id,
                TaskDispatch.delivered_at.is_(None),
                TaskDispatch.next_attempt_at <= now,
                or_(TaskDispatch.lease_until.is_(None), TaskDispatch.lease_until < now),
            )
            .values(lease_owner=owner, lease_until=now + timedelta(seconds=60))
        ).rowcount
        if not claimed:
            return False
        row = session.get(TaskDispatch, task_id)
        task = session.get(ResearchTask, task_id)
        if task.status not in {"queued", "enqueue_failed"}:
            row.delivered_at = now
            row.lease_owner = row.lease_until = None
            session.commit()
            return True
        task.status = "queued"
        job_id = row.job_id
        session.commit()
    error = None
    try:
        enqueuer(task_id, job_id)
    except Exception as exception:
        error = type(exception).__name__
    with factory() as session:
        row = session.scalar(
            select(TaskDispatch)
            .where(TaskDispatch.task_id == task_id, TaskDispatch.lease_owner == owner)
            .with_for_update()
        )
        if row is None:
            return False
        row.attempts += 1
        row.lease_owner = row.lease_until = None
        row.error_type = error
        if error:
            row.next_attempt_at = utcnow() + timedelta(seconds=min(300, 2 ** min(row.attempts, 8)))
            changed = session.execute(
                update(ResearchTask)
                .where(ResearchTask.id == task_id, ResearchTask.status == "queued")
                .values(status="enqueue_failed")
            ).rowcount
            if changed:
                from vendorlens.events import RunEventStore

                RunEventStore().append(
                    session, task_id, "run.enqueue_failed", {"error": "queue_unavailable"}
                )
        else:
            row.delivered_at = utcnow()
        session.commit()
    return error is None


def dispatch_pending(factory, enqueuer, limit=100):
    with factory() as session:
        ids = list(
            session.scalars(
                select(TaskDispatch.task_id)
                .where(
                    TaskDispatch.delivered_at.is_(None), TaskDispatch.next_attempt_at <= utcnow()
                )
                .order_by(TaskDispatch.next_attempt_at)
                .limit(limit)
            )
        )
    return sum(dispatch_one(factory, task_id, enqueuer) for task_id in ids)
