from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_tasks import seeded as seeded

from vendorlens.api import app, get_review_enqueuer
from vendorlens.config import Settings
from vendorlens.db import get_session
from vendorlens.models import ResearchTask


def test_session_preferences_task_snapshot_and_policy_reload_api(seeded, tmp_path, monkeypatch):
    settings = Settings(_env_file=None, skills_root=tmp_path)
    monkeypatch.setattr("vendorlens.tasks.get_settings", lambda: settings)
    monkeypatch.setattr("vendorlens.runtime_api.get_settings", lambda: settings)
    monkeypatch.setattr("vendorlens.conversation_memory.get_settings", lambda: settings)
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)

    def sessions():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_review_enqueuer] = lambda: lambda *_: None
    try:
        with TestClient(app) as client:
            base = "/projects/smoke-project"
            response = client.post(base + "/sessions", json={"user_id": "alice"})
            assert response.status_code == 201
            sid = response.json()["session_id"]
            path = base + f"/sessions/{sid}"
            assert (
                client.post(
                    path + "/messages?user_id=alice", json={"content": "只看本地部署"}
                ).status_code
                == 201
            )
            assert client.get(path + "/context?user_id=bob").status_code == 404
            assert (
                client.put(
                    base + "/preferences?user_id=alice", json={"comparison_months": 60}
                ).status_code
                == 200
            )
            policy = tmp_path / "review.json"
            policy.write_text('{"id":"review","version":"1","content":"核验引用"}')
            assert client.post("/skills/reload").status_code == 200
            result = client.post(
                base + "/tasks",
                json={
                    "objective": "比较五年成本",
                    "vendor_ids": ["smoke-vendor"],
                    "session_id": sid,
                    "user_id": "alice",
                },
            )
            assert result.status_code == 202
            task = seeded.get(ResearchTask, result.json()["task_id"])
            assert task.request_snapshot["comparison_months"] == 60
            assert task.request_snapshot["skill_snapshots"][0]["id"] == "review"
            assert task.request_snapshot["memory_context"]["recent_messages"]
            policy.write_text("invalid")
            assert client.post("/skills/reload").status_code == 422
            assert client.get("/skills").json()["skills"][0]["version"] == "1"
            assert client.get(f"/tasks/{task.id}/routing").json() == {"attempts": []}
            assert client.get("/routing/health").json()["configured"] is False
            assert (
                client.post(path + "/messages?user_id=alice", json={"content": "   "}).status_code
                == 422
            )
            assert (
                client.post(
                    base + "/tasks",
                    json={
                        "objective": "比较",
                        "session_id": sid,
                        "user_id": "bob",
                        "vendor_ids": ["smoke-vendor"],
                    },
                ).status_code
                == 404
            )
            assert len(list(seeded.scalars(select(ResearchTask)))) == 1
    finally:
        app.dependency_overrides.clear()
