import runpy
from pathlib import Path

SETUP = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/setup_local_env.py"))


def test_initialization_preserves_model_key_and_existing_external_database(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "VENDORLENS_MODEL_API_KEY=existing-test-model-key\n"
        "VENDORLENS_DATABASE_URL=postgresql://app@database.example:5432/app\n"
        "VENDORLENS_REDIS_URL=redis://localhost:6380/0\n"
    )
    SETUP["initialize"](tmp_path)
    values = SETUP["read_values"](env)
    assert values["VENDORLENS_MODEL_API_KEY"] == "existing-test-model-key"
    assert values["VENDORLENS_DATABASE_URL"] == "postgresql://app@database.example:5432/app"
    assert values["VENDORLENS_REDIS_PASSWORD"] in values["VENDORLENS_REDIS_URL"]
    frontend = tmp_path / "apps/web/.env.local"
    assert SETUP["read_values"](frontend)["VENDORLENS_API_ACCESS_KEY"] == values[
        "VENDORLENS_API_ACCESS_KEY"
    ]
    assert env.stat().st_mode & 0o777 == 0o600
    assert frontend.stat().st_mode & 0o777 == 0o600
    before = env.read_text(), frontend.read_text()
    SETUP["initialize"](tmp_path)
    assert before == (env.read_text(), frontend.read_text())


def test_initialization_from_template_expands_default_local_credentials(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / ".env.example").write_text((root / ".env.example").read_text())
    SETUP["initialize"](tmp_path)
    values = SETUP["read_values"](tmp_path / ".env")
    assert values["VENDORLENS_POSTGRES_PASSWORD"] in values["VENDORLENS_DATABASE_URL"]
    assert values["VENDORLENS_REDIS_PASSWORD"] in values["VENDORLENS_REDIS_URL"]
    assert "${" not in values["VENDORLENS_DATABASE_URL"]
    assert values["VENDORLENS_CHROMA_URL"] == "http://127.0.0.1:8100"
