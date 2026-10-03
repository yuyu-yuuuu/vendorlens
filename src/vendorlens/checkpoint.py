from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def checkpoint_serializer():
    names = (
        "EvidenceItem",
        "Claim",
        "ClaimType",
        "ClaimReview",
        "ReviewResult",
        "FinalReport",
        "ReviewDomain",
        "RequirementStatus",
        "CommitmentStatus",
        "DomainAssessmentStatus",
        "RecommendationLevel",
        "RequirementAssessment",
        "RequirementItem",
        "CostSourceItem",
        "CostAssessment",
        "CommitmentFinding",
        "CommitmentAssessment",
        "DomainAssessment",
        "VendorAssessment",
        "ReviewWeights",
        "VendorRecommendationScore",
        "RecommendationResult",
    )
    allowed = [("vendorlens.schemas", name) for name in names]
    allowed.append(("vendorlens.review_agent", "VendorReviewInput"))
    return JsonPlusSerializer(allowed_msgpack_modules=allowed, allowed_json_modules=allowed)


def checkpoint_connection_string(database_url: str) -> str:
    """Convert a SQLAlchemy psycopg URL to the DSN expected by LangGraph."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def postgres_checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    dsn = checkpoint_connection_string(database_url)
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver = PostgresSaver(saver.conn, serde=checkpoint_serializer())
        saver.setup()
        yield saver


@contextmanager
def sqlite_checkpointer(path):
    from langgraph.checkpoint.sqlite import SqliteSaver

    path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        yield SqliteSaver(saver.conn, serde=checkpoint_serializer())


class FencedCheckpointSaver(BaseCheckpointSaver):
    """Serialize checkpoint writes with recovery's task-row lock."""

    def __init__(self, saver, session_factory, task_id, owner):
        super().__init__(serde=saver.serde)
        self.saver = saver
        self.session_factory = session_factory
        self.task_id, self.owner = task_id, owner

    def get_tuple(self, config):
        return self.saver.get_tuple(config)

    def list(self, config, *, filter=None, before=None, limit=None):
        return self.saver.list(config, filter=filter, before=before, limit=limit)

    def get_next_version(self, current, channel):
        return self.saver.get_next_version(current, channel)

    def _write(self, method, *args):
        from vendorlens.recovery import own_task

        with self.session_factory() as session:
            own_task(session, self.task_id, self.owner)
            result = method(*args)
            session.commit()
            return result

    def put(self, config, checkpoint, metadata, new_versions):
        return self._write(self.saver.put, config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        return self._write(self.saver.put_writes, config, writes, task_id, task_path)
