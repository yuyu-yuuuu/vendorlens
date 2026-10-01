from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from vendorlens.schemas import Claim, ClaimReview, ClaimType, EvidenceItem, ReviewResult


@dataclass(frozen=True)
class SourceRecord:
    project_id: str
    version_id: str
    chunk_id: str
    text: str
    text_hash: str


class EvidenceRepository(Protocol):
    def get_source(self, chunk_id: str) -> SourceRecord | None: ...


NUMERIC_TYPES = {
    ClaimType.AMOUNT,
    ClaimType.PERCENTAGE,
    ClaimType.DURATION,
}


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _numeric_values(text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    pattern = re.compile(r"(?<![\d.])([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(万|%|％)?")
    for match in pattern.finditer(text):
        raw, suffix = match.groups()
        try:
            value = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        if suffix == "万":
            value *= 10_000
        values.add(value.normalize())
    return values


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

        if claim.claim_type in NUMERIC_TYPES:
            if claim.structured_value is None:
                errors.add("structured_value_missing")
            else:
                expected = _numeric_values(claim.structured_value)
                observed = _numeric_values(" ".join(valid_quotes))
                if not expected or not expected <= observed:
                    errors.add("numeric_value_mismatch")

        if claim.claim_type is ClaimType.DATE and claim.structured_value:
            normalized_date = claim.structured_value.replace("/", "-")
            normalized_quotes = " ".join(valid_quotes).replace("/", "-")
            if normalized_date not in normalized_quotes:
                errors.add("date_mismatch")

        codes = sorted(errors)
        return ClaimReview(
            claim_id=claim.id,
            passed=not codes,
            error_codes=codes,
            message="通过确定性证据校验" if not codes else "、".join(codes),
        )
