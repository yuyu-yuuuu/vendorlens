import pytest
from fastapi.testclient import TestClient

import vendorlens.auth as auth
from vendorlens.api import app
from vendorlens.config import Settings


@pytest.mark.parametrize("environment", ["production", "staging", "Production", "unknown"])
@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_credentials_fail_closed_outside_development(monkeypatch, environment, key):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(_env_file=None, environment=environment, api_access_key=key),
    )
    response = TestClient(app).get("/tasks/missing")
    assert response.status_code == 503
    assert response.json() == {"detail": "API access key is not configured"}


def test_production_rejects_requests_without_configured_bearer(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(_env_file=None, environment="production", api_access_key="test-key"),
    )
    client = TestClient(app)
    assert client.get("/tasks/missing").status_code == 401
    invalid = client.get("/tasks/missing", headers={"Authorization": "Bearer wrong"})
    assert invalid.status_code == 401
