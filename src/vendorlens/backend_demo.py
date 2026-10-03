"""Offline end-to-end backend demo; real storage/index/checkpoints, no model API."""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from pathlib import Path

import chromadb
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from vendorlens.checkpoint import sqlite_checkpointer
from vendorlens.coordinator import DatabaseRetrievalCoordinator
from vendorlens.db import Base, build_engine
from vendorlens.events import RunEventStore
from vendorlens.fallbacks import CommitmentRuleChecker, RequirementRuleEvaluator
from vendorlens.indexes import (
    ChromaVectorIndex,
    DatabaseParentRetriever,
    DocumentIndexingService,
    PersistentBM25Index,
    PersistentHybridRetriever,
)
from vendorlens.ingestion import IngestionService
from vendorlens.models import (
    AnalysisReport,
    DocumentChunk,
    Project,
    Requirement,
    ReviewConfig,
    Vendor,
)
from vendorlens.review_agent import VendorReviewAgent, VendorReviewDraft, VendorReviewExecutor
from vendorlens.review_tools import ReviewToolRegistry
from vendorlens.schemas import CostAssessment, ReviewDomain
from vendorlens.storage import LocalDocumentStorage
from vendorlens.tasks import ReviewTaskRequest, create_review_task, run_review_task
from vendorlens.tracing import get_active_trace


def run_demo(output: Path):
    # Never mix demo data with an existing project or overwrite an earlier run.
    output.mkdir(parents=True, exist_ok=False)
    engine = build_engine(f"sqlite:///{output / 'demo.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ingestion = IngestionService(LocalDocumentStorage(output / "files"))
    bm25 = PersistentBM25Index(output / "bm25.json")
    vector = ChromaVectorIndex(chromadb.PersistentClient(path=str(output / "chroma")))
    indexing = DocumentIndexingService(bm25, vector)
    vendor_ids = ["alpha", "cheap", "incomplete"]
    samples = [
        "支持私有化部署。\n软件许可及实施一次性费用80万元，年度维保10万元。\n合同签署后90天交付。",
        "不支持私有化部署。\n软件许可及实施一次性费用50万元，年度维保5万元。\n合同签署后90天交付。",
        "支持私有化部署。\n软件许可一次性费用40万元。\n合同签署后90天交付。",
    ]
    with factory() as session:
        session.add(Project(id="demo-project", name="离线供应商评审"))
        session.flush()
        session.add_all([Vendor(id=vid, project_id="demo-project", name=vid) for vid in vendor_ids])
        session.flush()
        buyer = ingestion.ingest(
            session,
            project_id="demo-project",
            filename="需求.md",
            media_type="text/markdown",
            content="必须支持私有化部署。".encode(),
        )
        indexing.index_version(session, buyer.version_id)
        chunk = session.scalar(
            select(DocumentChunk).where(
                DocumentChunk.version_id == buyer.version_id, DocumentChunk.chunk_type == "parent"
            )
        )
        session.add(
            Requirement(
                project_id="demo-project",
                code="R1",
                text="支持私有化部署",
                mandatory=True,
                source_chunk_id=chunk.id,
            )
        )
        session.add(ReviewConfig(project_id="demo-project", commitment_checklist=["delivery"]))
        for vid, text in zip(vendor_ids, samples, strict=True):
            result = ingestion.ingest(
                session,
                project_id="demo-project",
                vendor_id=vid,
                filename="方案.md",
                media_type="text/markdown",
                content=text.encode(),
            )
            indexing.index_version(session, result.version_id)
        session.commit()
        task = create_review_task(
            session,
            "demo-project",
            ReviewTaskRequest(
                objective="离线验证版本、隔离、成本、评分及追踪", vendor_ids=vendor_ids
            ),
        )
        session.commit()
        task_id = task.id

    def analyzer(data, _tools):
        registry = ReviewToolRegistry(data)
        costs = registry.call(
            "extract_cost_items",
            evidence_ids=[item.id for item in data.evidence_by_domain[ReviewDomain.COST_MODELING]],
        )
        totals = {"one_time": 0.0, "recurring": 0.0, "optional": 0.0}
        for item in costs:
            totals[item["category"]] += (
                item["annual_amount"] if item["category"] == "recurring" else item["amount"]
            )
        trace = get_active_trace()
        with trace.span("calculate_tco", "tool", argument_count=4):
            cost = registry.call(
                "calculate_tco",
                one_time_cost=totals["one_time"],
                annual_recurring_cost=totals["recurring"],
                optional_cost=totals["optional"],
                evidence_ids=[
                    item.id for item in data.evidence_by_domain[ReviewDomain.COST_MODELING]
                ],
            )
        requirements = RequirementRuleEvaluator().evaluate(
            data.vendor_id,
            data.requirements,
            {
                req.id: data.evidence_by_domain[ReviewDomain.REQUIREMENT_COMPLIANCE]
                for req in data.requirements
            },
        )
        commitments = CommitmentRuleChecker().evaluate(
            data.vendor_id, data.evidence_by_domain[ReviewDomain.COMMITMENT_VERIFICATION]
        )
        return VendorReviewDraft(
            requirement_findings=requirements.requirement_findings,
            cost_assessment=CostAssessment(**cost),
            commitment_assessment=commitments.commitment_assessment,
        )

    @contextmanager
    def workflow_factory(factory):
        from vendorlens.workflow import ReviewWorkflow

        with sqlite_checkpointer(output / "checkpoints.sqlite") as saver:
            yield ReviewWorkflow(
                DatabaseRetrievalCoordinator(
                    factory, DatabaseParentRetriever(PersistentHybridRetriever(bm25, vector))
                ),
                VendorReviewExecutor(VendorReviewAgent(analyzer)),
                checkpointer=saver,
            )

    run_review_task(task_id, factory, workflow_factory)
    with factory() as session:
        report = session.scalar(select(AnalysisReport).where(AnalysisReport.task_id == task_id))
        events = RunEventStore().after(session, task_id, 0)
        result = {
            "mode": "offline-deterministic",
            "paid_model_calls": 0,
            "vendors": len(vendor_ids),
            "task_id": task_id,
            "status": report.status,
            "report": report.content,
            "event_count": len(events),
        }
        scores = {item["vendor_id"]: item for item in report.content["comparison_matrix"]}
        assert scores["cheap"]["disqualification_reasons"]
        assert scores["incomplete"]["conditions"]
        assert report.content["executive_summary"] == "推荐：alpha。"
    (output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    engine.dispose()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="A new directory, never existing data"
    )
    result = run_demo(parser.parse_args().output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "vendors": result["vendors"],
                "summary": result["report"]["executive_summary"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
