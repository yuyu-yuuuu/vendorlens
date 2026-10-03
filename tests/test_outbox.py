from datetime import timedelta

from sqlalchemy.orm import sessionmaker

from vendorlens.models import Project, ResearchTask, TaskDispatch, utcnow
from vendorlens.outbox import dispatch_pending, schedule


def test_crash_before_enqueue_and_timeout_are_retried_without_changing_job_identity(session):
    session.add(Project(id="p", name="P"))
    session.flush()
    task = ResearchTask(project_id="p", objective="review")
    session.add(task)
    session.flush()
    schedule(session, task)
    session.commit()  # Simulate API death here, before Redis.
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    calls = []

    def uncertain(task_id, job_id):
        calls.append((task_id, job_id))
        raise TimeoutError("private provider text")

    assert dispatch_pending(factory, uncertain) == 0
    session.expire_all()
    row = session.get(TaskDispatch, task.id)
    assert row.error_type == "TimeoutError"
    assert session.get(ResearchTask, task.id).status == "enqueue_failed"
    row.next_attempt_at = utcnow() - timedelta(seconds=1)
    session.commit()
    assert dispatch_pending(factory, lambda *args: calls.append(args)) == 1
    assert calls == [(task.id, task.run_id)] * 2
    assert dispatch_pending(factory, lambda *args: calls.append(args)) == 0


def test_dispatch_lease_and_worker_claim_are_respected(session):
    session.add(Project(id="p", name="P"))
    session.flush()
    task = ResearchTask(project_id="p", objective="review")
    session.add(task)
    session.flush()
    row = schedule(session, task)
    row.lease_owner, row.lease_until = "busy", utcnow() + timedelta(seconds=30)
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    calls = []
    assert dispatch_pending(factory, lambda *args: calls.append(args)) == 0
    row.lease_until = utcnow() - timedelta(seconds=1)
    task.status = "running"
    session.commit()
    assert dispatch_pending(factory, lambda *args: calls.append(args)) == 1
    assert calls == []
