from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from vendorlens.cost_grounding import (
    CostGroundingError,
    evidence_text,
    extract_grounded_costs,
    validate_cost_numbers,
)
from vendorlens.models import Document, DocumentChunk, DocumentVersion
from vendorlens.numeric_facts import numeric_facts, unsupported_numeric_facts
from vendorlens.schemas import (
    Claim,
    ClaimReview,
    ClaimType,
    DomainAssessment,
    DomainAssessmentStatus,
    EvidenceItem,
    ReviewResult,
    VendorAssessment,
)

if TYPE_CHECKING:
    from vendorlens.review_agent import VendorReviewInput


@dataclass(frozen=True)
class SourceRecord:
    project_id: str
    version_id: str
    chunk_id: str
    text: str
    text_hash: str


class EvidenceRepository(Protocol):
    def get_source(self, chunk_id: str) -> SourceRecord | None: ...


class DatabaseEvidenceRepository:
    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]):
        self.session_factory = session_factory

    def get_source(self, chunk_id: str) -> SourceRecord | None:
        with self.session_factory() as session:
            row = session.execute(
                select(DocumentChunk, DocumentVersion, Document)
                .join(DocumentVersion, DocumentVersion.id == DocumentChunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(DocumentChunk.id == chunk_id)
            ).one_or_none()
            if row is None:
                return None
            chunk, version, document = row
            return SourceRecord(
                project_id=document.project_id,
                version_id=version.id,
                chunk_id=chunk.id,
                text=chunk.text,
                text_hash=chunk.text_hash,
            )


NUMERIC_TYPES = {
    ClaimType.AMOUNT,
    ClaimType.PERCENTAGE,
    ClaimType.DURATION,
}


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DeterministicEvidenceGate:
    def __init__(self, repository: EvidenceRepository):
        self.repository = repository

    def review(self, claims: list[Claim], evidence: list[EvidenceItem]) -> ReviewResult:
        evidence_by_id = {item.id: item for item in evidence}
        reviews = [self._review_claim(claim, evidence_by_id) for claim in claims]
        failed = [item.claim_id for item in reviews if not item.passed]
        return ReviewResult(
            passed=not failed,
            claim_reviews=reviews,
            revision_claim_ids=failed,
            missing_information=[
                f"Claim {item.claim_id}: {item.message}" for item in reviews if not item.passed
            ],
        )

    def _review_claim(self, claim: Claim, evidence_by_id: dict[str, EvidenceItem]) -> ClaimReview:
        errors: set[str] = set()
        if claim.is_factual and not claim.evidence_ids:
            errors.add("missing_evidence")

        valid_quotes: list[str] = []
        for evidence_id in claim.evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if item is None:
                errors.add("evidence_not_found")
                continue
            source = self.repository.get_source(item.chunk_id)
            if source is None:
                errors.add("source_not_found")
                continue
            if source.project_id != item.project_id:
                errors.add("project_mismatch")
            if source.version_id != item.document_version_id:
                errors.add("version_mismatch")
            if (
                source.text_hash != item.source_text_hash
                or source_hash(source.text) != item.source_text_hash
            ):
                errors.add("hash_mismatch")
            if item.quote not in source.text:
                errors.add("quote_not_in_source")
            else:
                valid_quotes.append(item.quote)

        if claim.claim_type in NUMERIC_TYPES or claim.claim_type is ClaimType.DATE:
            if claim.structured_value is None:
                errors.add("structured_value_missing")
            else:
                value = claim.structured_value
                if claim.unit and not any(f.kind != "number" for f in numeric_facts(value)):
                    value = f"{value} {claim.unit}"
                if not numeric_facts(value) or unsupported_numeric_facts(value, valid_quotes):
                    errors.add(
                        "date_mismatch"
                        if claim.claim_type is ClaimType.DATE
                        else "numeric_value_mismatch"
                    )
        # A correct structured value must not hide a contradictory statement.
        if unsupported_numeric_facts(claim.statement, valid_quotes):
            errors.add("numeric_statement_mismatch")

        codes = sorted(errors)
        return ClaimReview(
            claim_id=claim.id,
            passed=not codes,
            error_codes=codes,
            message="通过确定性证据校验" if not codes else "、".join(codes),
        )


def validate_numeric_output(
    input_data: VendorReviewInput,
    result: VendorAssessment,
) -> VendorAssessment:
    """Turn only unsupported domains into FAILED; discard their scoring payloads."""
    domains = dict(result.domains)
    for domain, assessment in domains.items():
        if assessment.status is DomainAssessmentStatus.FAILED:
            continue
        available = {
            item.id: item for item in input_data.evidence_by_domain.get(domain, input_data.evidence)
        }
        errors: list[str] = []

        def texts(ids: list[str], available=available, errors=errors) -> list[str]:
            if set(ids) - set(available):
                errors.append("domain_evidence_mismatch")
            return [evidence_text(available[item_id]) for item_id in ids if item_id in available]

        def check(text: str, ids: list[str], field: str, texts=texts, errors=errors) -> None:
            if unsupported_numeric_facts(text, texts(ids)):
                errors.append(f"numeric_fact_mismatch:{field}")

        grounded_cost = None
        texts(assessment.evidence_ids)
        for index, finding in enumerate(assessment.requirement_findings):
            check(finding.explanation, finding.evidence_ids, f"requirement[{index}]")
        commitment = assessment.commitment_assessment
        if commitment is not None:
            from vendorlens.schemas import CommitmentStatus

            present = {
                finding.category
                for finding in commitment.findings
                if finding.status is not CommitmentStatus.NOT_STATED
            }
            missing = [
                f"必要承诺未说明: {category}"
                for category in input_data.required_commitments
                if category not in present
            ]
            commitment = commitment.model_copy(
                update={
                    "missing_information": list(
                        dict.fromkeys(commitment.missing_information + missing)
                    ),
                }
            )
            assessment = assessment.model_copy(
                update={
                    "commitment_assessment": commitment,
                    "missing_information": list(
                        dict.fromkeys(assessment.missing_information + missing)
                    ),
                }
            )
            for index, finding in enumerate(commitment.findings):
                check(finding.statement, finding.evidence_ids, f"commitment[{index}]")
        cost = assessment.cost_assessment
        if cost is not None:
            texts(cost.evidence_ids)
            grounded = extract_grounded_costs(
                [available[item_id] for item_id in cost.evidence_ids if item_id in available],
                input_data.currency,
            ).require_categories(input_data.required_cost_categories)
            try:
                validate_cost_numbers(
                    grounded,
                    one_time=cost.one_time_cost,
                    recurring=cost.recurring_cost,
                    optional=cost.optional_cost,
                    tco=cost.total_cost_of_ownership,
                    comparison_months=input_data.comparison_months,
                )
            except (CostGroundingError, ValueError) as error:
                errors.append(str(error))
            # Always rebuild provenance locally; model-provided spans are not trusted.
            cost = cost.model_copy(
                update={
                    "cost_sources": grounded.items,
                    "missing_costs": list(dict.fromkeys(cost.missing_costs + grounded.missing)),
                }
            )
            assessment = assessment.model_copy(
                update={
                    "cost_assessment": cost,
                    "missing_information": list(
                        dict.fromkeys(assessment.missing_information + grounded.missing)
                    ),
                }
            )
            for index, term in enumerate(cost.payment_terms):
                check(term, cost.evidence_ids, f"payment_terms[{index}]")
            if not errors:
                grounded_cost = grounded.result(
                    vendor_id=input_data.vendor_id,
                    currency=input_data.currency,
                    comparison_months=input_data.comparison_months,
                )
            for field in ("missing_costs", "conflicts"):
                for text in getattr(cost, field):
                    _check_cost_note(text, texts(cost.evidence_ids), grounded_cost, errors, field)
        ids = list(
            dict.fromkeys(
                assessment.evidence_ids
                + [e for f in assessment.requirement_findings for e in f.evidence_ids]
                + ([e for f in commitment.findings for e in f.evidence_ids] if commitment else [])
                + (cost.evidence_ids if cost else [])
            )
        )
        for text in assessment.missing_information + assessment.conflicts:
            if cost:
                _check_cost_note(text, texts(ids), grounded_cost, errors, "domain_notes")
            else:
                check(text, ids, "domain_notes")
        if commitment:
            for text in commitment.missing_information + commitment.conflicts:
                check(text, ids, "commitment_notes")
        domains[domain] = (
            DomainAssessment(
                domain=domain,
                vendor_id=input_data.vendor_id,
                status=DomainAssessmentStatus.FAILED,
                missing_information=list(dict.fromkeys(errors)),
                revision_count=assessment.revision_count,
            )
            if errors
            else assessment
        )
    return VendorAssessment(vendor_id=result.vendor_id, domains=domains)


def _check_cost_note(
    text: str, sources: list[str], derived: dict | None, errors: list[str], field: str
) -> None:
    for clause in re.split(r"[，；;。\n]", text):
        allowed = list(sources)
        if derived and re.search(r"(?<![A-Za-z])TCO(?![A-Za-z])|总拥有成本", clause, re.IGNORECASE):
            # Explicit derivation label required; a payment amount cannot borrow the TCO value.
            allowed.append(
                f"{derived['total_cost_of_ownership']} {derived['currency']} "
                f"比较周期{derived['comparison_months']}个月"
            )
        if unsupported_numeric_facts(clause, allowed):
            errors.append(f"numeric_fact_mismatch:{field}")
