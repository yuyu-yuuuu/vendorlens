from contextlib import nullcontext
from pathlib import Path

import chromadb

from vendorlens.coordinator import DatabaseRetrievalCoordinator
from vendorlens.indexes import (
    ChromaVectorIndex,
    DatabaseParentRetriever,
    DocumentIndexingService,
    PersistentBM25Index,
    PersistentHybridRetriever,
)
from vendorlens.ingestion import IngestionService
from vendorlens.models import Project, Vendor
from vendorlens.schemas import (
    PlanDimension,
    RequirementItem,
    ResearchPlan,
    ResearchSubtask,
    ReviewDomain,
)
from vendorlens.storage import LocalDocumentStorage


def test_coordinator_searches_only_latest_indexed_document_version(session, tmp_path: Path):
    project = Project(name="供应商评审")
    session.add(project)
    session.flush()
    ingestion = IngestionService(LocalDocumentStorage(tmp_path / "files"))
    old = ingestion.ingest(
        session,
        project_id=project.id,
        filename="报价.md",
        media_type="text/markdown",
        content="# 报价\n旧版总报价 99 万元。".encode(),
    )
    new = ingestion.ingest(
        session,
        project_id=project.id,
        filename="报价.md",
        media_type="text/markdown",
        content="# 报价\n新版总报价 88 万元。".encode(),
    )
    bm25 = PersistentBM25Index(tmp_path / "bm25.json")
    vector = ChromaVectorIndex(chromadb.EphemeralClient(), collection_name="coordinator")
    indexer = DocumentIndexingService(bm25, vector)
    indexer.index_version(session, old.version_id)
    indexer.index_version(session, new.version_id)
    coordinator = DatabaseRetrievalCoordinator(
        lambda: nullcontext(session),
        DatabaseParentRetriever(PersistentHybridRetriever(bm25, vector)),
    )
    plan = ResearchPlan(
        objective="比较报价",
        dimensions=[PlanDimension(name="价格", weight=1.0, description="总价")],
        subtasks=[
            ResearchSubtask(
                id="price",
                title="报价",
                queries=["总报价"],
                dimension_names=["价格"],
            )
        ],
    )

    evidence = coordinator.retrieve(plan, project.id)["price"]

    assert evidence
    assert all(item.document_version_id == new.version_id for item in evidence)
    assert "88 万元" in evidence[0].quote
    assert "99 万元" not in evidence[0].quote


def test_vendor_package_contains_buyer_and_current_vendor_but_not_competitor(
    session, tmp_path: Path
):
    project = Project(name="CRM 供应商评审")
    vendor_a = Vendor(project=project, name="甲供应商")
    vendor_b = Vendor(project=project, name="乙供应商")
    session.add_all([project, vendor_a, vendor_b])
    session.flush()
    ingestion = IngestionService(LocalDocumentStorage(tmp_path / "files"))
    buyer = ingestion.ingest(
        session,
        project_id=project.id,
        filename="采购需求.md",
        media_type="text/markdown",
        content="# 必选需求\n系统必须支持私有化部署。".encode(),
    )
    vendor_a_old = ingestion.ingest(
        session,
        project_id=project.id,
        vendor_id=vendor_a.id,
        filename="甲方案.md",
        media_type="text/markdown",
        content="# 方案\n甲供应商旧版不支持私有化部署，报价 90 万元。".encode(),
    )
    vendor_a_new = ingestion.ingest(
        session,
        project_id=project.id,
        vendor_id=vendor_a.id,
        filename="甲方案.md",
        media_type="text/markdown",
        content="# 方案\n甲供应商支持私有化部署，报价 80 万元，90 天交付。".encode(),
    )
    vendor_b_version = ingestion.ingest(
        session,
        project_id=project.id,
        vendor_id=vendor_b.id,
        filename="乙方案.md",
        media_type="text/markdown",
        content="# 方案\n乙供应商支持私有化部署，报价 50 万元，60 天交付。".encode(),
    )
    bm25 = PersistentBM25Index(tmp_path / "vendor-bm25.json")
    vector = ChromaVectorIndex(chromadb.EphemeralClient(), collection_name="vendor-package")
    indexer = DocumentIndexingService(bm25, vector)
    for version_id in (
        buyer.version_id,
        vendor_a_old.version_id,
        vendor_a_new.version_id,
        vendor_b_version.version_id,
    ):
        indexer.index_version(session, version_id)
    coordinator = DatabaseRetrievalCoordinator(
        lambda: nullcontext(session),
        DatabaseParentRetriever(PersistentHybridRetriever(bm25, vector)),
    )

    package = coordinator.retrieve_vendor_evidence(
        project_id=project.id,
        vendor_id=vendor_a.id,
        queries_by_domain={
            ReviewDomain.REQUIREMENT_COMPLIANCE: ["私有化部署"],
            ReviewDomain.COST_MODELING: ["报价 万元"],
            ReviewDomain.COMMITMENT_VERIFICATION: ["交付 天"],
        },
    )
    input_data = package.to_review_input(
        task_id="task-a",
        requirements=[RequirementItem(id="req-1", text="支持私有化部署", mandatory=True)],
        currency="CNY",
        comparison_months=36,
        required_commitments=["delivery"],
    )

    assert set(package.evidence_by_domain) == set(ReviewDomain)
    assert buyer.version_id in package.document_version_ids
    assert vendor_a_new.version_id in package.document_version_ids
    assert vendor_a_old.version_id not in package.document_version_ids
    assert vendor_b_version.version_id not in package.document_version_ids
    assert input_data.vendor_id == vendor_a.id
    assert set(input_data.document_version_ids) == set(package.document_version_ids)
    assert set(input_data.evidence_by_domain) == set(ReviewDomain)
    assert {item.vendor_id for item in input_data.evidence} <= {None, vendor_a.id}
    assert all("乙供应商" not in item.quote for item in input_data.evidence)
