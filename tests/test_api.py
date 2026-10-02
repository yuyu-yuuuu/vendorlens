from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from vendorlens.api import app
from vendorlens.db import Base, get_session


def test_health_checks_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/health")
        assert response.status_code == 200
        assert response.json()["database"] == "ok"
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_swagger_and_redoc_are_available_without_database():
    client = TestClient(app)
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text
    assert "VendorLens 接口文档" in response.text
    assert "/openapi.json" in response.text
    assert client.get("/redoc").status_code == 200


def test_openapi_documents_existing_routes_and_response_contracts():
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert len(schema["paths"]) == 6
    assert {tag["name"] for tag in schema["tags"]} == {
        "健康检查",
        "评审任务",
        "进度事件",
        "执行追踪",
    }
    post = schema["paths"]["/projects/{project_id}/tasks"]["post"]
    assert post["summary"] == "创建并入队供应商评审任务"
    assert {"202", "404", "422", "503"} <= set(post["responses"])
    assert post["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TaskResponse"
    }
    models = schema["components"]["schemas"]
    assert models["ReviewTaskRequest"]["examples"][0]["vendor_ids"]
    assert "description" in models["ReviewTaskRequest"]["properties"]["queries_by_domain"]
    assert (
        models["ReportResponse"]["properties"]["report"]["$ref"]
        == "#/components/schemas/FinalReport"
    )
    assert "description" in models["TraceSummary"]["properties"]["usage_complete"]


def test_sse_openapi_is_stream_not_json_and_describes_resume_header():
    schema = TestClient(app).get("/openapi.json").json()
    events = schema["paths"]["/tasks/{task_id}/events"]["get"]
    content = events["responses"]["200"]["content"]
    assert set(content) == {"text/event-stream"}
    assert "event: node.completed" in content["text/event-stream"]["example"]
    parameters = {(param["name"], param["in"]): param for param in events["parameters"]}
    assert ("last-event-id", "header") in parameters
    assert "优先于" in parameters[("last-event-id", "header")]["description"]
    assert parameters[("after", "query")]["schema"]["minimum"] == 0
