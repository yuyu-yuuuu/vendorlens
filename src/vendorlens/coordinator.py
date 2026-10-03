from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from vendorlens.domain import ExpandedSearchHit
from vendorlens.indexes import DatabaseParentRetriever
from vendorlens.models import Document, DocumentVersion, Vendor
from vendorlens.review_agent import VendorReviewInput
from vendorlens.schemas import EvidenceItem, RequirementItem, ResearchPlan, ReviewDomain


@dataclass(frozen=True)
class VendorEvidencePackage:
    project_id: str
    vendor_id: str
    document_version_ids: tuple[str, ...]
    evidence_by_domain: dict[ReviewDomain, list[EvidenceItem]]

    @property
    def evidence(self) -> list[EvidenceItem]:
        unique = {item.id: item for items in self.evidence_by_domain.values() for item in items}
        return list(unique.values())

    def to_review_input(
        self,
        *,
        task_id: str,
        requirements: Sequence[RequirementItem],
        currency: str,
        comparison_months: int,
        required_cost_categories: Sequence[str] = (),
        required_commitments: Sequence[str] = (),
        project_context: str = "",
    ) -> VendorReviewInput:
        if not self.document_version_ids:
            raise ValueError("cannot build review input without indexed document versions")
        return VendorReviewInput(
            task_id=task_id,
            project_id=self.project_id,
            vendor_id=self.vendor_id,
            document_version_ids=list(self.document_version_ids),
            evidence=self.evidence,
            evidence_by_domain=self.evidence_by_domain,
            requirements=list(requirements),
            currency=currency,
            comparison_months=comparison_months,
            required_cost_categories=list(required_cost_categories),
            required_commitments=list(required_commitments),
            project_context=project_context,
        )


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

    def retrieve_vendor_evidence(
        self,
        *,
        project_id: str,
        vendor_id: str,
        queries_by_domain: dict[ReviewDomain, list[str]],
    ) -> VendorEvidencePackage:
        if set(queries_by_domain) != set(ReviewDomain):
            raise ValueError("vendor retrieval requires queries for every review domain")
        with self.session_factory() as session:
            vendor_exists = session.scalar(
                select(Vendor.id).where(
                    Vendor.id == vendor_id,
                    Vendor.project_id == project_id,
                )
            )
            if vendor_exists is None:
                raise ValueError("vendor does not belong to the project")
            version_ids = self._latest_indexed_version_ids_for_vendor(
                session, project_id, vendor_id
            )
            filters = {"project_id": project_id, "version_id": version_ids}
            evidence_by_domain = {
                domain: self._retrieve_subtask(session, queries, filters) if version_ids else []
                for domain, queries in queries_by_domain.items()
            }
            return VendorEvidencePackage(
                project_id=project_id,
                vendor_id=vendor_id,
                document_version_ids=tuple(version_ids),
                evidence_by_domain=evidence_by_domain,
            )

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

    @staticmethod
    def _latest_indexed_version_ids_for_vendor(
        session: Session,
        project_id: str,
        vendor_id: str,
    ) -> list[str]:
        rows = session.execute(
            select(DocumentVersion.id, DocumentVersion.document_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.project_id == project_id,
                or_(Document.vendor_id.is_(None), Document.vendor_id == vendor_id),
                DocumentVersion.status == "indexed",
            )
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
            context_text=hit.parent.text,
            matched_chunk_id=hit.matched_child.chunk_id,
            source_text_hash=metadata["text_hash"],
            location=metadata["location"],
            vendor_id=metadata.get("vendor_id"),
        )
