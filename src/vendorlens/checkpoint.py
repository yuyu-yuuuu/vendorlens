from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver


def checkpoint_connection_string(database_url: str) -> str:
    """Convert a SQLAlchemy psycopg URL to the DSN expected by LangGraph."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def postgres_checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    dsn = checkpoint_connection_string(database_url)
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()
        yield saver
