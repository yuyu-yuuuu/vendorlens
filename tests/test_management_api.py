import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from vendorlens.api import app, get_review_enqueuer
from vendorlens.config import Settings
from vendorlens.db import get_session
from vendorlens.models import DocumentVersion
from vendorlens.retrieval_runtime import get_indexing_service


@pytest.fixture
def client(session, tmp_path, monkeypatch):
    import vendorlens.management_api as management

    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)

    def sessions():
        with factory() as db:
            yield db

    class Index:
        def index_version(self, db, version_id):
            db.get(DocumentVersion, version_id).status = "indexed"
            return 1

    settings = Settings(_env_file=None, storage_root=tmp_path / "files", upload_max_bytes=10000)
    monkeypatch.setattr(management, "get_settings", lambda: settings)
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_indexing_service] = Index
    app.dependency_overrides[get_review_enqueuer] = lambda: lambda *_: None
    with TestClient(app) as connection:
        yield connection
    app.dependency_overrides.clear()


def setup_project(client):
    project = client.post("/projects", json={"name": "评审", "description": "合成"}).json()["id"]
    vendor = client.post(f"/projects/{project}/vendors", json={"name": "Alpha"}).json()["id"]
    return project, vendor


def upload(client, project, text, vendor=None):
    return client.post(
        f"/projects/{project}/documents",
        files={"file": ("材料.md", text.encode(), "text/markdown")},
        data={"vendor_id": vendor} if vendor else {},
    )


def test_empty_database_to_review_task_through_management_api(client):
    project, vendor = setup_project(client)
    uploaded = upload(client, project, "# 需求\n必须支持私有化部署。")
    assert uploaded.status_code == 201
    version = uploaded.json()["version_id"]
    chunks = client.get(f"/projects/{project}/versions/{version}/chunks").json()
    response = client.post(
        f"/projects/{project}/requirements",
        json={
            "code": "R1",
            "text": "支持私有化部署",
            "source_chunk_id": chunks[0]["id"],
            "mandatory": True,
        },
    )
    assert response.status_code == 201
    config = client.put(
        f"/projects/{project}/review-config",
        json={
            "comparison_months": 18,
            "required_cost_categories": ["software"],
        },
    )
    assert config.status_code == 200
    task = client.post(
        f"/projects/{project}/tasks", json={"objective": "比较方案", "vendor_ids": [vendor]}
    )
    assert task.status_code == 202
    assert len(client.get(f"/projects/{project}/tasks").json()) == 1
    assert client.get(f"/projects/{project}/review-config").json()["comparison_months"] == 18
    assert len(client.get("/projects").json()) == 1
    assert client.put(f"/projects/{project}", json={"name": "改名"}).status_code == 200
    assert (
        client.put(f"/projects/{project}/vendors/{vendor}", json={"name": "Beta"}).status_code
        == 200
    )


def test_upload_is_versioned_idempotent_and_source_is_readable(client):
    project, vendor = setup_project(client)
    first = upload(client, project, "实施费100元", vendor).json()
    same = upload(client, project, "实施费100元", vendor).json()
    assert first["version_id"] == same["version_id"] and not same["created"]
    changed = upload(client, project, "实施费200元", vendor).json()
    assert changed["version_number"] == 2
    versions = client.get(f"/projects/{project}/documents/{first['document_id']}/versions").json()
    assert len(versions) == 2
    assert (
        client.get(f"/projects/{project}/versions/{first['version_id']}/file").content
        == "实施费100元".encode()
    )
    assert len(client.get(f"/projects/{project}/documents").json()) == 1


def test_foreign_vendor_source_and_version_are_rejected(client):
    first, vendor = setup_project(client)
    second = client.post("/projects", json={"name": "另一个项目"}).json()["id"]
    assert upload(client, second, "文字", vendor).status_code == 422
    uploaded = upload(client, first, "供应商方案", vendor).json()
    version = uploaded["version_id"]
    assert client.get(f"/projects/{second}/versions/{version}/chunks").status_code == 404
    chunk = client.get(f"/projects/{first}/versions/{version}/chunks").json()[0]
    assert (
        client.post(
            f"/projects/{first}/requirements",
            json={
                "code": "R1",
                "text": "需求",
                "source_chunk_id": chunk["id"],
            },
        ).status_code
        == 422
    )
    assert client.post(f"/projects/{first}/vendors", json={"name": "Alpha"}).status_code == 409


def test_upload_size_format_and_config_validation(client):
    project, _ = setup_project(client)
    assert upload(client, project, "").status_code == 422
    assert upload(client, project, "a" * 10001).status_code == 413
    assert (
        client.post(
            f"/projects/{project}/documents", files={"file": ("bad.exe", b"bad")}
        ).status_code
        == 415
    )
    assert (
        client.put(
            f"/projects/{project}/review-config", json={"requirement_weight": 0.9}
        ).status_code
        == 422
    )
    assert client.post("/projects", json={"name": "  "}).status_code == 422
    assert client.get("/projects/missing").status_code == 404


def test_index_failure_retains_parsed_version_and_can_be_retried(client, session):
    class BrokenIndex:
        def index_version(self, *args):
            raise RuntimeError("provider-secret-do-not-expose")

    original = app.dependency_overrides[get_indexing_service]
    app.dependency_overrides[get_indexing_service] = BrokenIndex
    project, _ = setup_project(client)
    response = upload(client, project, "报价100元")
    assert response.status_code == 503 and "provider-secret" not in response.text
    version_id = response.json()["detail"]["version_id"]
    session.expire_all()
    assert session.get(DocumentVersion, version_id).status == "index_failed"
    app.dependency_overrides[get_indexing_service] = original
    assert client.post(f"/projects/{project}/versions/{version_id}/index").status_code == 200
