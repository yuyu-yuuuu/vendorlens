from pathlib import Path

from sqlalchemy import select

from vendorlens.indexes import DocumentIndexingService, PersistentBM25Index
from vendorlens.ingestion import IngestionService
from vendorlens.models import Document, Project, Vendor
from vendorlens.storage import LocalDocumentStorage


class RecordingIndex:
    def __init__(self):
        self.chunks = []

    def replace_version(self, version_id, chunks):
        self.chunks = chunks


def test_same_filename_is_isolated_between_buyer_and_vendors(session, tmp_path: Path):
    project = Project(name="ERP 选型")
    vendor_a = Vendor(project=project, name="甲公司")
    vendor_b = Vendor(project=project, name="乙公司")
    session.add_all([project, vendor_a, vendor_b])
    session.flush()
    ingestion = IngestionService(LocalDocumentStorage(tmp_path / "files"))

    buyer = ingestion.ingest(
        session,
        project_id=project.id,
        filename="方案.md",
        media_type="text/markdown",
        content="采购方需求".encode(),
    )
    first = ingestion.ingest(
        session,
        project_id=project.id,
        vendor_id=vendor_a.id,
        filename="方案.md",
        media_type="text/markdown",
        content="甲公司方案".encode(),
    )
    second = ingestion.ingest(
        session,
        project_id=project.id,
        vendor_id=vendor_b.id,
        filename="方案.md",
        media_type="text/markdown",
        content="乙公司方案".encode(),
    )

    assert len({buyer.document_id, first.document_id, second.document_id}) == 3
    documents = list(session.scalars(select(Document).order_by(Document.vendor_id)))
    assert {item.vendor_id for item in documents} == {None, vendor_a.id, vendor_b.id}


def test_ingestion_rejects_vendor_from_another_project(session, tmp_path: Path):
    project = Project(name="ERP 选型")
    other = Project(name="CRM 选型")
    vendor = Vendor(project=other, name="外部供应商")
    session.add_all([project, other, vendor])
    session.flush()

    try:
        IngestionService(LocalDocumentStorage(tmp_path / "files")).ingest(
            session,
            project_id=project.id,
            vendor_id=vendor.id,
            filename="方案.md",
            media_type="text/markdown",
            content="错误归属".encode(),
        )
    except ValueError as error:
        assert str(error) == "vendor does not belong to project"
    else:
        raise AssertionError("cross-project vendor must be rejected")


def test_vendor_id_is_written_to_retrieval_metadata(session, tmp_path: Path):
    project = Project(name="ERP 选型")
    vendor = Vendor(project=project, name="甲公司")
    session.add_all([project, vendor])
    session.flush()
    result = IngestionService(LocalDocumentStorage(tmp_path / "files")).ingest(
        session,
        project_id=project.id,
        vendor_id=vendor.id,
        filename="方案.md",
        media_type="text/markdown",
        content="# 报价\n总报价 88 万元。".encode(),
    )
    bm25 = PersistentBM25Index(tmp_path / "bm25.json")
    vector = RecordingIndex()

    DocumentIndexingService(bm25, vector).index_version(session, result.version_id)

    assert bm25.chunks
    assert all(item.metadata["vendor_id"] == vendor.id for item in bm25.chunks)
    assert all(item.metadata["vendor_id"] == vendor.id for item in vector.chunks)
