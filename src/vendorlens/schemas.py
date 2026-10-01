from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ClaimType(StrEnum):
    FACT = "fact"
    AMOUNT = "amount"
    DATE = "date"
    PERCENTAGE = "percentage"
    DURATION = "duration"
    RECOMMENDATION = "recommendation"


class PlanDimension(BaseModel):
    name: str
    weight: float = Field(ge=0, le=1)
    description: str


class ResearchSubtask(BaseModel):
    id: str
    title: str
    queries: list[str] = Field(min_length=1)
    dimension_names: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    objective: str
    dimensions: list[PlanDimension] = Field(min_length=1)
    subtasks: list[ResearchSubtask] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_weights_and_references(self) -> ResearchPlan:
        if abs(sum(item.weight for item in self.dimensions) - 1.0) > 1e-6:
            raise ValueError("dimension weights must sum to 1")
        dimensions = {item.name for item in self.dimensions}
        task_ids = {item.id for item in self.subtasks}
        if len(task_ids) != len(self.subtasks):
            raise ValueError("subtask ids must be unique")
        for task in self.subtasks:
            if not set(task.dimension_names) <= dimensions:
                raise ValueError(f"subtask {task.id} references an unknown dimension")
            if task.id in task.depends_on or not set(task.depends_on) <= task_ids:
                raise ValueError(f"subtask {task.id} has an invalid dependency")
        return self


class EvidenceItem(BaseModel):
    id: str
    project_id: str
    document_version_id: str
    chunk_id: str
    quote: str = Field(min_length=1)
    source_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    location: dict[str, Any]


class Claim(BaseModel):
    id: str
    subtask_id: str
    statement: str = Field(min_length=1)
    claim_type: ClaimType
    evidence_ids: list[str] = Field(default_factory=list)
    structured_value: str | None = None
    unit: str | None = None
    confidence: float = Field(ge=0, le=1)

    @property
    def is_factual(self) -> bool:
        return self.claim_type is not ClaimType.RECOMMENDATION


class ClaimReview(BaseModel):
    claim_id: str
    passed: bool
    error_codes: list[str] = Field(default_factory=list)
    message: str


class ReviewResult(BaseModel):
    passed: bool
    claim_reviews: list[ClaimReview]
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    revision_claim_ids: list[str] = Field(default_factory=list)


class FinalReport(BaseModel):
    executive_summary: str
    comparison_matrix: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    missing_information: list[str]
    claim_evidence_matrix: list[dict[str, Any]]
