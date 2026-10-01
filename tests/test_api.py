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
