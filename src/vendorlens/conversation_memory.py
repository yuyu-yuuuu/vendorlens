"""Three time scales. Historical discussion is context, never authorized evidence."""

import hashlib
import json
from datetime import timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from vendorlens.config import get_settings
from vendorlens.coordinator import DatabaseRetrievalCoordinator
from vendorlens.models import (
    ConversationMessage,
    ConversationSession,
    EpisodicMemory,
    UserPreference,
    utcnow,
)
from vendorlens.retrieval import HashEmbeddingProvider, HybridRetriever, IndexedChunk


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comparison_months: int | None = Field(default=None, gt=0, le=1200)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    report_style: Literal["concise", "detailed"] | None = None
    focus_areas: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=10
    )


class ConversationMemory:
    def __init__(self, settings=None, *, redis_client=None, vector_collection=None):
        self.settings = settings or get_settings()
        self.redis = redis_client
        self.vector = vector_collection
        self.cache_status = "database"
        self.retrieval_status = "local_hybrid"

    @staticmethod
    def scoped_session(db, session_id, project_id, user_id):
        row = db.get(ConversationSession, session_id)
        if row is None or row.project_id != project_id or row.user_id != user_id:
            raise LookupError("conversation session not found in this scope")
        return row

    def _redis(self):
        if self.redis is None and self.settings.memory_redis_enabled:
            from redis import Redis

            self.redis = Redis.from_url(
                self.settings.redis_url, socket_timeout=1, socket_connect_timeout=1
            )
        return self.redis

    def _vector(self):
        if self.vector is None and self.settings.memory_chroma_enabled:
            from urllib.parse import urlparse

            import chromadb

            parsed = urlparse(self.settings.chroma_url)
            client = chromadb.HttpClient(
                host=parsed.hostname or "localhost",
                port=parsed.port or 8000,
                ssl=parsed.scheme == "https",
            )
            self.vector = client.get_or_create_collection("vendorlens_episodic_hash_v1")
        return self.vector

    def recent(self, db, conversation):
        cutoff = utcnow() - timedelta(seconds=self.settings.memory_ttl_seconds)
        rows = list(
            db.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.session_id == conversation.id,
                    ConversationMessage.compressed.is_(False),
                    ConversationMessage.created_at >= cutoff,
                )
                .order_by(ConversationMessage.sequence.desc())
                .limit(self.settings.memory_recent_messages)
            )
        )
        payload = [
            {"role": m.role, "content": m.content, "sequence": m.sequence} for m in reversed(rows)
        ]
        # DB remains authoritative. Cache only accepted if identical to canonical rows.
        key = (
            "vendorlens:working:"
            + hashlib.sha256(
                f"{conversation.project_id}:{conversation.user_id}:{conversation.id}".encode()
            ).hexdigest()
        )
        try:
            redis = self._redis()
            if redis is not None:
                raw = redis.get(key)
                if raw and json.loads(raw) == payload:
                    self.cache_status = "redis_hit"
                    return json.loads(raw)
                redis.setex(key, self.settings.memory_ttl_seconds, json.dumps(payload))
                self.cache_status = "redis_refreshed"
        except Exception:
            self.cache_status = "redis_unavailable"
        return payload

    def append(self, db, conversation, role, content, *, task_id=None):
        if role not in {"user", "assistant"} or not content.strip() or len(content) > 8000:
            raise ValueError("invalid conversation message")
        # Lock the session row for concurrent append/compression on PostgreSQL.
        db.execute(
            select(ConversationSession)
            .where(ConversationSession.id == conversation.id)
            .with_for_update()
        ).scalar_one()
        sequence = (
            db.scalar(
                select(func.max(ConversationMessage.sequence)).where(
                    ConversationMessage.session_id == conversation.id
                )
            )
            or 0
        ) + 1
        row = ConversationMessage(
            session_id=conversation.id,
            sequence=sequence,
            role=role,
            content=content,
            task_id=task_id,
        )
        db.add(row)
        db.flush()
        self.compress(db, conversation)
        return row

    def compress(self, db, conversation):
        rows = list(
            db.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.session_id == conversation.id,
                    ConversationMessage.compressed.is_(False),
                )
                .order_by(ConversationMessage.sequence)
            )
        )
        if len(rows) < self.settings.memory_compress_at:
            return
        old = rows[: -self.settings.memory_recent_messages]
        task_ids = {m.task_id for m in old if m.task_id}
        sources = (
            list(db.scalars(select(EpisodicMemory).where(EpisodicMemory.task_id.in_(task_ids))))
            if task_ids
            else []
        )
        summary = (
            "历史讨论摘录（非事实证据）：\n"
            + "\n".join(f"{m.role}: {m.content[:300]}" for m in old)[:3500]
        )
        db.add(
            EpisodicMemory(
                project_id=conversation.project_id,
                user_id=conversation.user_id,
                session_id=conversation.id,
                summary=summary,
                source_versions=list(dict.fromkeys(v for m in sources for v in m.source_versions)),
                evidence_ids=list(dict.fromkeys(e for m in sources for e in m.evidence_ids)),
            )
        )
        for row in old:
            row.compressed = True
        db.flush()

    @staticmethod
    def preferences(db, project_id, user_id):
        row = db.scalar(
            select(UserPreference).where(
                UserPreference.project_id == project_id, UserPreference.user_id == user_id
            )
        )
        return row.preferences if row else {}

    def set_preferences(self, db, project_id, user_id, preferences):
        payload = Preferences.model_validate(preferences).model_dump(exclude_none=True)
        row = db.scalar(
            select(UserPreference).where(
                UserPreference.project_id == project_id, UserPreference.user_id == user_id
            )
        )
        if row is None:
            row = UserPreference(project_id=project_id, user_id=user_id)
            db.add(row)
        # Only this explicit API writes stable preferences; messages never infer them.
        row.preferences = payload
        db.flush()
        return payload

    def context(self, db, conversation, query, vendor_ids):
        current = set(
            DatabaseRetrievalCoordinator._latest_indexed_version_ids(db, conversation.project_id)
        )
        rows = list(
            db.scalars(
                select(EpisodicMemory)
                .where(
                    EpisodicMemory.project_id == conversation.project_id,
                    EpisodicMemory.user_id == conversation.user_id,
                )
                .order_by(EpisodicMemory.created_at.desc())
                .limit(200)
            )
        )
        valid = [
            m
            for m in rows
            if set(m.source_versions) <= current
            and (m.vendor_id is None or m.vendor_id in vendor_ids)
        ]
        ordered = self._rank(valid, query)
        episodes = [
            {
                "id": m.id,
                "summary": m.summary,
                "task_id": m.task_id,
                "vendor_id": m.vendor_id,
                "source_versions": m.source_versions,
                "evidence_ids": m.evidence_ids,
                "context_only": True,
            }
            for m in ordered[:3]
        ]
        return {
            "recent_messages": self.recent(db, conversation),
            "episodes": episodes,
            "preferences": self.preferences(db, conversation.project_id, conversation.user_id),
            "stale_episodes_excluded": sum(not set(m.source_versions) <= current for m in rows),
            "retrieval_backend": self.retrieval_status,
            "working_backend": self.cache_status,
        }

    def _rank(self, rows, query):
        if not rows:
            return []
        by_id = {m.id: m for m in rows}
        try:
            vector = self._vector()
            if vector is not None:
                vectors = HashEmbeddingProvider().embed([m.summary for m in rows])
                vector.upsert(
                    ids=[m.id for m in rows],
                    documents=[m.summary for m in rows],
                    embeddings=vectors,
                    metadatas=[{"memory_id": m.id} for m in rows],
                )
                result = vector.query(
                    query_embeddings=HashEmbeddingProvider().embed([query]),
                    n_results=min(3, len(rows)),
                    where={"memory_id": {"$in": list(by_id)}},
                )
                ranked = [by_id[i] for i in result["ids"][0] if i in by_id]
                if ranked:
                    self.retrieval_status = "chroma_hash"
                    return ranked
        except Exception:
            self.retrieval_status = "chroma_unavailable_local_hybrid"
        retriever = HybridRetriever([IndexedChunk(m.id, m.summary, {}) for m in rows])
        return [by_id[h.chunk_id] for h in retriever.search(query, top_k=3)]

    def record_result(self, db, task, result):
        scope = task.request_snapshot.get("conversation")
        if not scope:
            return
        conversation = self.scoped_session(
            db, scope["session_id"], task.project_id, scope["user_id"]
        )
        for vendor_id, assessment in result["assessments"].items():
            input_data = result.get("review_inputs", {}).get(vendor_id)
            summary = f"评审目标：{task.objective}\n供应商：{vendor_id}\n" + "\n".join(
                f"{d.value}: {a.status.value}; 待补：{'、'.join(a.missing_information)}"
                for d, a in assessment.domains.items()
            )
            score = result["recommendation"].vendor_scores.get(vendor_id)
            if score:
                summary += (
                    f"\n本供应商得分：{score.total_score}; 限制：{'、'.join(score.conditions)}"
                )
            db.add(
                EpisodicMemory(
                    project_id=task.project_id,
                    user_id=conversation.user_id,
                    session_id=conversation.id,
                    task_id=task.id,
                    vendor_id=vendor_id,
                    summary=summary[:4000],
                    source_versions=input_data.document_version_ids if input_data else [],
                    evidence_ids=list(
                        dict.fromkeys(
                            e for a in assessment.domains.values() for e in a.evidence_ids
                        )
                    ),
                )
            )
        self.append(
            db,
            conversation,
            "assistant",
            result["report"].executive_summary[:8000],
            task_id=task.id,
        )


def vendor_memory(context, vendor_id, current_versions=None):
    """Avoid passing a competitor's historical assessment to the selected vendor."""
    return {
        **context,
        "episodes": [
            m
            for m in context.get("episodes", [])
            if m.get("vendor_id") in (None, vendor_id)
            and (
                current_versions is None
                or set(m.get("source_versions", [])) <= set(current_versions)
            )
        ],
    }
