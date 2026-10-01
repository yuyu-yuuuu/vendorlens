from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy import select
from sqlalchemy.orm import Session

from vendorlens.domain import ExpandedSearchHit
from vendorlens.indexes import DatabaseParentRetriever
from vendorlens.models import Document, DocumentVersion
from vendorlens.schemas import EvidenceItem, ResearchPlan


class DatabaseRetrievalCoordinator:
    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]],
        retriever: DatabaseParentRetriever,
        *,
        top_k_per_subtask: int = 5,
    ):
        self.session_factory = session_factory
        self.retriever = retriever
        self.top_k_per_subtask = top_k_per_subtask

    def retrieve(self, plan: ResearchPlan, project_id: str) -> dict[str, list[EvidenceItem]]:
        with self.session_factory() as session:
            version_ids = self._latest_indexed_version_ids(session, project_id)
            if not version_ids:
                return {subtask.id: [] for subtask in plan.subtasks}
            filters = {"project_id": project_id, "version_id": version_ids}
            return {
                subtask.id: self._retrieve_subtask(session, subtask.queries, filters)
                for subtask in plan.subtasks
            }

    @staticmethod
    def _latest_indexed_version_ids(session: Session, project_id: str) -> list[str]:
        rows = session.execute(
            select(DocumentVersion.id, DocumentVersion.document_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.project_id == project_id, DocumentVersion.status == "indexed")
            .order_by(DocumentVersion.document_id, DocumentVersion.version_number.desc())
        )
        latest_by_document: dict[str, str] = {}
        for version_id, document_id in rows:
            latest_by_document.setdefault(document_id, version_id)
        return list(latest_by_document.values())

    def _retrieve_subtask(
        self, session: Session, queries: list[str], filters: dict
    ) -> list[EvidenceItem]:
        scores: dict[str, float] = {}
        hits: dict[str, ExpandedSearchHit] = {}
        for query in queries:
            query_hits = self.retriever.search(
                session,
                query,
                top_k=self.top_k_per_subtask * 2,
                filters=filters,
            )
            for rank, hit in enumerate(query_hits, 1):
                parent_id = hit.parent.chunk_id
                scores[parent_id] = scores.get(parent_id, 0.0) + 1.0 / (60 + rank)
                hits.setdefault(parent_id, hit)
        ordered = sorted(scores, key=lambda item: (-scores[item], item))[: self.top_k_per_subtask]
        return [self._to_evidence(hits[parent_id]) for parent_id in ordered]

    @staticmethod
    def _to_evidence(hit: ExpandedSearchHit) -> EvidenceItem:
        metadata = hit.parent.metadata
        return EvidenceItem(
            id=f"evidence:{hit.parent.chunk_id}",
            project_id=metadata["project_id"],
            document_version_id=metadata["version_id"],
            chunk_id=hit.parent.chunk_id,
            quote=hit.matched_child.text,
            source_text_hash=metadata["text_hash"],
            location=metadata["location"],
        )
