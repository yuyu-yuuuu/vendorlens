import hashlib
import json
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
from vendorlens.model_analyzer import VendorReviewModelAnalyzer
from vendorlens.models import Project, Vendor
from vendorlens.schemas import (
    PlanDimension,
    RequirementItem,
    ResearchPlan,
    ResearchSubtask,
    ReviewDomain,
)
from vendorlens.storage import LocalDocumentStorage


def test_full_parent_reaches_model_while_quote_remains_the_matched_child(session, tmp_path):
    project = Project(name="上下文测试")
    vendor = Vendor(project=project, name="甲供应商")
    session.add_all([project, vendor])
    session.flush()
    parent_text = (
        "支持私有化部署。" + "系统提供常规业务功能。" * 35 + "仅限企业版，需额外购买授权。"
    )
    ingested = IngestionService(LocalDocumentStorage(tmp_path / "files")).ingest(
        session,
        project_id=project.id,
        vendor_id=vendor.id,
        filename="方案.md",
        media_type="text/markdown",
        content=parent_text.encode(),
    )
    bm25 = PersistentBM25Index(tmp_path / "context-bm25.json")
    vector = ChromaVectorIndex(chromadb.EphemeralClient(), collection_name="parent-context")
    DocumentIndexingService(bm25, vector).index_version(session, ingested.version_id)
    coordinator = DatabaseRetrievalCoordinator(
        lambda: nullcontext(session),
        DatabaseParentRetriever(PersistentHybridRetriever(bm25, vector)),
    )
    package = coordinator.retrieve_vendor_evidence(
        project_id=project.id,
        vendor_id=vendor.id,
        queries_by_domain={domain: ["私有化部署"] for domain in ReviewDomain},
    )
    input_data = package.to_review_input(
        task_id="context-task",
        requirements=[RequirementItem(id="req", text="私有化部署")],
        currency="CNY",
        comparison_months=36,
    )
    item = input_data.evidence[0]
    assert item.context_text == parent_text
    assert item.source_text_hash == hashlib.sha256(parent_text.encode()).hexdigest()
    assert item.document_version_id == ingested.version_id
    assert item.matched_chunk_id != item.chunk_id
    assert item.quote in parent_text
    assert len(item.quote) <= 320
    assert item.quote != parent_text
    # 不依赖融合检索的平分排序：至少一个条件在命中子块外、父块内。
    assert not ("支持私有化部署" in item.quote and "仅限企业版" in item.quote)
    messages = VendorReviewModelAnalyzer._messages(input_data)
    payload = json.loads(messages[1]["content"].split("输入：", 1)[1])
    assert payload["evidence"][0]["context_text"] == parent_text
    assert payload["evidence"][0]["quote"] == item.quote
    assert payload["evidence"][0]["matched_chunk_id"] == item.matched_chunk_id
    assert "限定条件" in messages[0]["content"]


def test_standalone_row_can_be_both_context_and_quote():
    from vendorlens.domain import ExpandedSearchHit, SearchHit

    text = "实施费用 | 800000"
    hit = SearchHit(
        chunk_id="row-1",
        text=text,
        score=1,
        rank=1,
        strategy="bm25",
        metadata={
            "project_id": "project-1",
            "version_id": "version-1",
            "text_hash": hashlib.sha256(text.encode()).hexdigest(),
            "location": {"sheet": "报价", "cell_range": "A1:B1"},
        },
    )
    item = DatabaseRetrievalCoordinator._to_evidence(
        ExpandedSearchHit(parent=hit, matched_child=hit)
    )
    assert item.context_text == item.quote == text
    assert item.matched_chunk_id == item.chunk_id == "row-1"


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
