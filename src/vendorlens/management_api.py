"""Management, immutable documents and source inspection endpoints."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vendorlens.config import get_settings
from vendorlens.db import get_session
from vendorlens.ingestion import IngestionService
from vendorlens.models import (
    Document,
    DocumentChunk,
    DocumentVersion,
    Project,
    Requirement,
    ResearchTask,
    ReviewConfig,
    Vendor,
)
from vendorlens.retrieval_runtime import get_indexing_service
from vendorlens.schemas import ReviewWeights
from vendorlens.storage import LocalDocumentStorage

router = APIRouter(tags=["项目与资料管理"])
DB = Annotated[Session, Depends(get_session)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)


class VendorInput(Input):
    name: str = Field(min_length=1, max_length=200)


class RequirementInput(Input):
    code: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=10000)
    source_chunk_id: str
    mandatory: bool = False
    weight: float = Field(default=1, gt=0, allow_inf_nan=False)


class ConfigInput(Input):
    requirement_weight: float = Field(default=0.5, ge=0, le=1)
    cost_weight: float = Field(default=0.3, ge=0, le=1)
    commitment_weight: float = Field(default=0.2, ge=0, le=1)
    comparison_months: int = Field(default=36, gt=0, le=1200)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    required_cost_categories: list[str] = Field(
        default_factory=lambda: ["software", "implementation", "maintenance"],
        max_length=30,
    )
    commitment_checklist: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("required_cost_categories", "commitment_checklist")
    @classmethod
    def unique_checklist(cls, values):
        if any(not value.strip() or len(value) > 80 for value in values):
            raise ValueError("checklist entries must be nonblank and at most 80 characters")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def weights(self):
        ReviewWeights(
            requirement_compliance=self.requirement_weight,
            cost_competitiveness=self.cost_weight,
            commitment=self.commitment_weight,
        )
        return self


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime


class VendorResponse(BaseModel):
    id: str
    project_id: str
    name: str
    created_at: datetime


class RequirementResponse(RequirementInput):
    id: str
    project_id: str


class VersionResponse(BaseModel):
    id: str
    document_id: str
    version_number: int
    file_hash: str
    status: str
    created_at: datetime


class UploadResponse(BaseModel):
    document_id: str
    version_id: str
    version_number: int
    created: bool
    chunk_count: int
    status: str
    indexed_chunk_count: int


def row_data(row, fields):
    return {field: getattr(row, field) for field in fields}


PROJECT_FIELDS = ("id", "name", "description", "created_at")
VENDOR_FIELDS = ("id", "project_id", "name", "created_at")
REQUIREMENT_FIELDS = ("id", "project_id", "code", "text", "source_chunk_id", "mandatory", "weight")
VERSION_FIELDS = ("id", "document_id", "version_number", "file_hash", "status", "created_at")


def project_exists(session, project_id):
    row = session.get(Project, project_id)
    if row is None:
        raise HTTPException(404, "project not found")
    return row


def commit(session):
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "a record with this name or code already exists") from None


def version_in_project(session, project_id, version_id):
    project_exists(session, project_id)
    version = session.scalar(
        select(DocumentVersion)
        .join(Document)
        .where(
            DocumentVersion.id == version_id,
            Document.project_id == project_id,
        )
    )
    if version is None:
        raise HTTPException(404, "document version not found in project")
    return version


@router.post("/projects", status_code=201, response_model=ProjectResponse, summary="创建评审项目")
def create_project(request: ProjectInput, session: DB):
    row = Project(**request.model_dump())
    session.add(row)
    commit(session)
    return row_data(row, PROJECT_FIELDS)


@router.get("/projects", response_model=list[ProjectResponse], summary="分页列出项目")
def list_projects(session: DB, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return [
        row_data(row, PROJECT_FIELDS)
        for row in session.scalars(
            select(Project).order_by(Project.created_at, Project.id).offset(offset).limit(limit)
        )
    ]


@router.get("/projects/{project_id}", response_model=ProjectResponse, summary="读取评审项目")
def get_project(project_id: str, session: DB):
    return row_data(project_exists(session, project_id), PROJECT_FIELDS)


@router.put("/projects/{project_id}", response_model=ProjectResponse, summary="更新项目名称与说明")
def update_project(project_id: str, request: ProjectInput, session: DB):
    row = project_exists(session, project_id)
    for key, value in request.model_dump().items():
        setattr(row, key, value)
    commit(session)
    return row_data(row, PROJECT_FIELDS)


@router.post(
    "/projects/{project_id}/vendors",
    status_code=201,
    response_model=VendorResponse,
    summary="登记供应商",
)
def create_vendor(project_id: str, request: VendorInput, session: DB):
    project_exists(session, project_id)
    row = Vendor(project_id=project_id, name=request.name)
    session.add(row)
    commit(session)
    return row_data(row, VENDOR_FIELDS)


@router.get(
    "/projects/{project_id}/vendors", response_model=list[VendorResponse], summary="列出项目供应商"
)
def list_vendors(project_id: str, session: DB):
    project_exists(session, project_id)
    return [
        row_data(row, VENDOR_FIELDS)
        for row in session.scalars(
            select(Vendor).where(Vendor.project_id == project_id).order_by(Vendor.name)
        )
    ]


@router.put(
    "/projects/{project_id}/vendors/{vendor_id}",
    response_model=VendorResponse,
    summary="更新供应商名称",
)
def update_vendor(project_id: str, vendor_id: str, request: VendorInput, session: DB):
    row = session.scalar(
        select(Vendor).where(Vendor.id == vendor_id, Vendor.project_id == project_id)
    )
    if row is None:
        raise HTTPException(404, "vendor not found in project")
    row.name = request.name
    commit(session)
    return row_data(row, VENDOR_FIELDS)


def validate_requirement_source(session, project_id, source_chunk_id):
    source = session.scalar(
        select(DocumentChunk)
        .join(DocumentVersion)
        .join(Document)
        .where(
            DocumentChunk.id == source_chunk_id,
            Document.project_id == project_id,
            Document.vendor_id.is_(None),
        )
    )
    if source is None:
        raise HTTPException(422, "requirement source must be a buyer chunk in this project")


@router.post(
    "/projects/{project_id}/requirements",
    status_code=201,
    response_model=RequirementResponse,
    summary="登记可追溯需求",
)
def create_requirement(project_id: str, request: RequirementInput, session: DB):
    project_exists(session, project_id)
    validate_requirement_source(session, project_id, request.source_chunk_id)
    row = Requirement(project_id=project_id, **request.model_dump())
    session.add(row)
    commit(session)
    return row_data(row, REQUIREMENT_FIELDS)


@router.get(
    "/projects/{project_id}/requirements",
    response_model=list[RequirementResponse],
    summary="列出需求目录",
)
def list_requirements(project_id: str, session: DB):
    project_exists(session, project_id)
    return [
        row_data(row, REQUIREMENT_FIELDS)
        for row in session.scalars(
            select(Requirement)
            .where(Requirement.project_id == project_id)
            .order_by(Requirement.code)
        )
    ]


@router.put(
    "/projects/{project_id}/requirements/{requirement_id}",
    response_model=RequirementResponse,
    summary="更新未来任务的需求",
)
def update_requirement(
    project_id: str, requirement_id: str, request: RequirementInput, session: DB
):
    row = session.scalar(
        select(Requirement).where(
            Requirement.id == requirement_id,
            Requirement.project_id == project_id,
        )
    )
    if row is None:
        raise HTTPException(404, "requirement not found in project")
    validate_requirement_source(session, project_id, request.source_chunk_id)
    for key, value in request.model_dump().items():
        setattr(row, key, value)
    commit(session)
    return row_data(row, REQUIREMENT_FIELDS)


@router.get(
    "/projects/{project_id}/review-config",
    response_model=ConfigInput,
    summary="读取评审配置与默认值",
)
def get_config(project_id: str, session: DB):
    project_exists(session, project_id)
    row = session.scalar(select(ReviewConfig).where(ReviewConfig.project_id == project_id))
    return row_data(row, tuple(ConfigInput.model_fields)) if row else ConfigInput().model_dump()


@router.put(
    "/projects/{project_id}/review-config",
    response_model=ConfigInput,
    summary="设置权重、比较口径与必需条目",
)
def put_config(project_id: str, request: ConfigInput, session: DB):
    project_exists(session, project_id)
    row = session.scalar(select(ReviewConfig).where(ReviewConfig.project_id == project_id))
    if row is None:
        row = ReviewConfig(project_id=project_id)
        session.add(row)
    for key, value in request.model_dump().items():
        setattr(row, key, value)
    commit(session)
    return row_data(row, tuple(ConfigInput.model_fields))


@router.post(
    "/projects/{project_id}/documents",
    status_code=201,
    response_model=UploadResponse,
    summary="上传、解析并索引资料",
)
def upload_document(
    project_id: str,
    session: DB,
    file: Annotated[UploadFile, File()],
    indexing: Annotated[object, Depends(get_indexing_service)],
    vendor_id: Annotated[str | None, Form()] = None,
):
    project_exists(session, project_id)
    if (
        vendor_id
        and session.scalar(
            select(Vendor.id).where(
                Vendor.id == vendor_id,
                Vendor.project_id == project_id,
            )
        )
        is None
    ):
        raise HTTPException(422, "vendor must belong to project")
    filename = Path((file.filename or "").replace("\\", "/")).name
    if Path(filename).suffix.lower() not in {".pdf", ".docx", ".xlsx", ".md", ".txt"}:
        raise HTTPException(415, "unsupported document format")
    settings = get_settings()
    content = file.file.read(settings.upload_max_bytes + 1)
    if not content:
        raise HTTPException(422, "empty document")
    if len(content) > settings.upload_max_bytes:
        raise HTTPException(413, "document exceeds upload limit")
    try:
        result = IngestionService(LocalDocumentStorage(settings.storage_root)).ingest(
            session,
            project_id=project_id,
            vendor_id=vendor_id,
            filename=filename,
            media_type=file.content_type or "application/octet-stream",
            content=content,
        )
        commit(session)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise HTTPException(422, "document parsing failed") from None
    return _index_response(session, result.version_id, indexing, asdict(result))


def _index_response(session, version_id, indexing, data):
    try:
        count = indexing.index_version(session, version_id)
        session.commit()
    except Exception:
        session.rollback()
        version = session.get(DocumentVersion, version_id)
        version.status = "index_failed"
        session.commit()
        raise HTTPException(503, {"error": "index_unavailable", "version_id": version_id}) from None
    return {**data, "status": "indexed", "indexed_chunk_count": count}


@router.get("/projects/{project_id}/documents", summary="列出供应商或采购方资料")
def list_documents(project_id: str, session: DB):
    project_exists(session, project_id)
    return [
        row_data(row, ("id", "vendor_id", "logical_name", "media_type"))
        for row in session.scalars(
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at, Document.id)
        )
    ]


@router.get(
    "/projects/{project_id}/documents/{document_id}/versions",
    response_model=list[VersionResponse],
    summary="列出不可变版本",
)
def list_versions(project_id: str, document_id: str, session: DB):
    document = session.get(Document, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(404, "document not found in project")
    return [
        row_data(row, VERSION_FIELDS)
        for row in session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number)
        )
    ]


@router.get("/projects/{project_id}/versions/{version_id}/chunks", summary="回查父子原文与定位")
def get_chunks(project_id: str, version_id: str, session: DB):
    version_in_project(session, project_id, version_id)
    return [
        row_data(
            row, ("id", "ordinal", "chunk_type", "parent_chunk_id", "text", "text_hash", "location")
        )
        for row in session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.version_id == version_id)
            .order_by(DocumentChunk.ordinal)
        )
    ]


@router.get("/projects/{project_id}/versions/{version_id}/file", summary="下载版本原始文件")
def get_file(project_id: str, version_id: str, session: DB):
    version = version_in_project(session, project_id, version_id)
    try:
        content = LocalDocumentStorage(get_settings().storage_root).read(version.storage_key)
    except OSError:
        raise HTTPException(404, "stored file not found") from None
    return Response(content, media_type="application/octet-stream")


@router.post("/projects/{project_id}/versions/{version_id}/index", summary="幂等重试版本索引")
def reindex(
    project_id: str,
    version_id: str,
    session: DB,
    indexing: Annotated[object, Depends(get_indexing_service)],
):
    version_in_project(session, project_id, version_id)
    return _index_response(session, version_id, indexing, {"version_id": version_id})


@router.get("/projects/{project_id}/tasks", summary="分页读取项目任务")
def list_tasks(
    project_id: str, session: DB, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)
):
    project_exists(session, project_id)
    return [
        row_data(row, ("id", "run_id", "trace_id", "status", "attempt", "created_at"))
        for row in session.scalars(
            select(ResearchTask)
            .where(ResearchTask.project_id == project_id)
            .order_by(ResearchTask.created_at.desc(), ResearchTask.id)
            .offset(offset)
            .limit(limit)
        )
    ]
