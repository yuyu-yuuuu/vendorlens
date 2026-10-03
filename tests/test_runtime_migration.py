from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from vendorlens import models  # noqa: F401
from vendorlens.config import get_settings
from vendorlens.db import Base


def test_runtime_migration_upgrade_downgrade_matches_models(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    runtime_tables = {
        "conversation_sessions",
        "conversation_messages",
        "episodic_memories",
        "user_preferences",
        "routing_health",
        "routing_attempts",
        "task_dispatches",
    }
    engine = create_engine(url)
    # Verify the new revision against the existing schema, not older PostgreSQL-only DDL.
    Base.metadata.create_all(
        engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in runtime_tables]
    )
    monkeypatch.setenv("VENDORLENS_DATABASE_URL", url)
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    try:
        command.stamp(cfg, "ca8f02b541d1")
        command.upgrade(cfg, "head")
        assert runtime_tables <= set(inspect(engine).get_table_names())
        command.check(cfg)
        command.downgrade(cfg, "ca8f02b541d1")
        assert not runtime_tables & set(inspect(engine).get_table_names())
        command.upgrade(cfg, "head")
        command.check(cfg)
    finally:
        engine.dispose()
        get_settings.cache_clear()
