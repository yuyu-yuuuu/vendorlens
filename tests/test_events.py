from vendorlens.events import RunEventStore, format_sse
from vendorlens.models import Project, ResearchTask


def test_events_are_ordered_and_resumable(session):
    project = Project(name="ERP 选型")
    session.add(project)
    session.flush()
    task = ResearchTask(project_id=project.id, objective="选择供应商")
    session.add(task)
    session.flush()
    store = RunEventStore()
    first = store.append(session, task.id, "plan.started", {"node": "plan"})
    second = store.append(session, task.id, "plan.completed", {"subtasks": 3})
    session.commit()

    resumed = store.after(session, task.id, first.sequence)

    assert [event.id for event in resumed] == [second.id]
    assert format_sse(second) == ('id: 2\nevent: plan.completed\ndata: {"subtasks":3}\n\n')


def test_event_requires_existing_task(session):
    store = RunEventStore()
    try:
        store.append(session, "missing", "run.started", {})
    except ValueError as error:
        assert str(error) == "research task does not exist"
    else:
        raise AssertionError("missing task must be rejected")
