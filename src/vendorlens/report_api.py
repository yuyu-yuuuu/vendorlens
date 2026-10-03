"""Read completed domain results and their frozen source evidence for the workbench."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from vendorlens.db import get_session
from vendorlens.models import Document, DocumentVersion, DomainAssessmentRecord, ResearchTask
from vendorlens.schemas import EvidenceItem

router = APIRouter(tags=["评审任务"])


@router.get("/tasks/{task_id}/assessments", summary="读取领域结果及本次评审的原文引用快照")
def get_assessments(task_id: str, db: Annotated[Session, Depends(get_session)]):
    task = db.get(ResearchTask, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    results, evidence = [], {}
    for row in db.scalars(
        select(DomainAssessmentRecord)
        .where(DomainAssessmentRecord.task_id == task_id)
        .order_by(DomainAssessmentRecord.vendor_id, DomainAssessmentRecord.domain)
    ):
        payload = dict(row.payload)
        snapshots = payload.pop("source_evidence", [])
        results.append(payload)
        for snapshot in snapshots:
            item = EvidenceItem.model_validate(snapshot)
            if item.project_id != task.project_id or item.vendor_id not in (None, row.vendor_id):
                continue
            if item.id not in payload.get("evidence_ids", []):
                continue
            source = db.execute(
                select(Document.logical_name, DocumentVersion.version_number)
                .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                .where(
                    Document.project_id == task.project_id,
                    DocumentVersion.id == item.document_version_id,
                )
            ).one_or_none()
            evidence[item.id] = {
                **item.model_dump(mode="json"),
                "document_name": source[0] if source else "原始资料",
                "version_number": source[1] if source else None,
                "source_available": source is not None,
            }
    return {
        "task_id": task_id,
        "project_id": task.project_id,
        "objective": task.objective,
        "comparison_months": task.request_snapshot.get("comparison_months"),
        "currency": task.request_snapshot.get("currency"),
        "assessments": results,
        "evidence": list(evidence.values()),
    }
