from contextlib import nullcontext

from sqlalchemy import select

from vendorlens.evidence import (
    DatabaseEvidenceRepository,
    DeterministicEvidenceGate,
    SourceRecord,
    source_hash,
)
from vendorlens.ingestion import IngestionService
from vendorlens.models import DocumentChunk, Project
from vendorlens.schemas import Claim, ClaimType, EvidenceItem
from vendorlens.storage import LocalDocumentStorage


class MemoryEvidenceRepository:
    def __init__(self, source: SourceRecord):
        self.source = source

    def get_source(self, chunk_id: str):
        return self.source if chunk_id == self.source.chunk_id else None


def build_source():
    text = "AlphaTech 含税总报价为 88 万元，计划在合同签署后 45 天交付。"
    return SourceRecord("project-1", "version-2", "chunk-1", text, source_hash(text))


def build_evidence(source: SourceRecord):
    return EvidenceItem(
        id="ev-1",
        project_id=source.project_id,
        document_version_id=source.version_id,
        chunk_id=source.chunk_id,
        quote="含税总报价为 88 万元",
        source_text_hash=source.text_hash,
        location={"sheet": "报价", "cell_range": "A2:B2"},
    )


def test_amount_claim_passes_with_versioned_exact_evidence():
    source = build_source()
    gate = DeterministicEvidenceGate(MemoryEvidenceRepository(source))
    claim = Claim(
        id="claim-1",
        subtask_id="price",
        statement="AlphaTech 含税总报价为 880000 元。",
        claim_type=ClaimType.AMOUNT,
        structured_value="880000",
        unit="CNY",
        confidence=0.95,
        evidence_ids=["ev-1"],
    )

    result = gate.review([claim], [build_evidence(source)])

    assert result.passed is True
    assert result.revision_claim_ids == []


def test_numeric_mismatch_is_rejected_even_when_quote_exists():
    source = build_source()
    gate = DeterministicEvidenceGate(MemoryEvidenceRepository(source))
    claim = Claim(
        id="claim-1",
        subtask_id="price",
        statement="AlphaTech 含税总报价为 89 万元。",
        claim_type=ClaimType.AMOUNT,
        structured_value="890000",
        unit="CNY",
        confidence=0.95,
        evidence_ids=["ev-1"],
    )

    review = gate.review([claim], [build_evidence(source)]).claim_reviews[0]

    assert review.passed is False
    assert "numeric_value_mismatch" in review.error_codes


def test_hash_and_version_mismatch_are_rejected():
    source = build_source()
    evidence = build_evidence(source).model_copy(
        update={"document_version_id": "version-old", "source_text_hash": "0" * 64}
    )
    claim = Claim(
        id="claim-1",
        subtask_id="price",
        statement="AlphaTech 有明确报价。",
        claim_type=ClaimType.FACT,
        confidence=0.9,
        evidence_ids=["ev-1"],
    )

    review = (
        DeterministicEvidenceGate(MemoryEvidenceRepository(source))
        .review([claim], [evidence])
        .claim_reviews[0]
    )

    assert set(review.error_codes) == {"hash_mismatch", "version_mismatch"}


def test_factual_claim_without_evidence_is_returned_for_revision():
    source = build_source()
    claim = Claim(
        id="claim-unsupported",
        subtask_id="delivery",
        statement="供应商支持零停机迁移。",
        claim_type=ClaimType.FACT,
        confidence=0.4,
    )

    result = DeterministicEvidenceGate(MemoryEvidenceRepository(source)).review([claim], [])

    assert result.passed is False
    assert result.revision_claim_ids == ["claim-unsupported"]
    assert result.claim_reviews[0].error_codes == ["missing_evidence"]


def test_database_repository_reads_versioned_parent_source(session, tmp_path):
    project = Project(name="供应商评审")
    session.add(project)
    session.flush()
    result = IngestionService(LocalDocumentStorage(tmp_path / "files")).ingest(
        session,
        project_id=project.id,
        filename="报价.md",
        media_type="text/markdown",
        content="# 报价\n含税总报价为 88 万元。".encode(),
    )
    parent_id = session.scalar(
        select(DocumentChunk.id).where(
            DocumentChunk.version_id == result.version_id,
            DocumentChunk.parent_chunk_id.is_(None),
        )
    )
    assert parent_id is not None

    source = DatabaseEvidenceRepository(lambda: nullcontext(session)).get_source(parent_id)

    assert source is not None
    assert source.project_id == project.id
    assert source.version_id == result.version_id
    assert source.text_hash == source_hash(source.text)
