from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_tasks import request, workflow_factory
from test_tasks import seeded as seeded

from vendorlens.api import app
from vendorlens.db import get_session
from vendorlens.models import DomainAssessmentRecord, ResearchTask
from vendorlens.tasks import create_review_task, run_review_task


def test_report_details_keep_frozen_evidence_and_hide_other_task(seeded):
    task = create_review_task(seeded, "smoke-project", request())
    seeded.commit()
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)
    assert run_review_task(task.id, factory, workflow_factory)

    def sessions():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = sessions
    try:
        client = TestClient(app)
        result = client.get(f"/tasks/{task.id}/assessments")
        assert result.status_code == 200
        data = result.json()
        assert len(data["assessments"]) == 3
        assert data["evidence"]
        frozen = data["evidence"][0]
        assert frozen["project_id"] == "smoke-project"
        assert frozen["quote"]
        if frozen["context_text"] is not None:
            assert frozen["quote"] in frozen["context_text"]
        assert client.get("/tasks/missing/assessments").status_code == 404
        other = ResearchTask(project_id="smoke-project", objective="另一次评审")
        seeded.add(other)
        seeded.commit()
        assert client.get(f"/tasks/{other.id}/assessments").json()["evidence"] == []
        assert client.get(f"/tasks/{other.id}/assessments").json()["assessments"] == []

        # Older domain records have no snapshot: never replace them with latest materials.
        for row in seeded.scalars(
            select(DomainAssessmentRecord).where(DomainAssessmentRecord.task_id == task.id)
        ):
            row.payload = {k: v for k, v in row.payload.items() if k != "source_evidence"}
        seeded.commit()
        old = client.get(f"/tasks/{task.id}/assessments").json()
        assert len(old["assessments"]) == 3
        assert old["evidence"] == []
    finally:
        app.dependency_overrides.clear()
