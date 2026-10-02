from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from vendorlens.fallbacks import DeterministicCostEngine
from vendorlens.retrieval import tokenize
from vendorlens.review_agent import (
    REVIEW_TOOLS_BY_DOMAIN,
    ReviewToolAccessError,
    VendorReviewInput,
)
from vendorlens.schemas import EvidenceItem, RequirementStatus, ReviewDomain


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


ID_LIST = {"type": "array", "items": {"type": "string"}, "minItems": 1}
NUMBER = {"type": "number", "minimum": 0}


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_requirement_evidence": _object_schema(
        {"requirement_id": {"type": "string"}, "query": {"type": "string"}},
        ["requirement_id", "query"],
    ),
    "compare_requirement_response": _object_schema(
        {"requirement_id": {"type": "string"}, "evidence_ids": ID_LIST},
        ["requirement_id", "evidence_ids"],
    ),
    "mark_missing_response": _object_schema(
        {"requirement_id": {"type": "string"}}, ["requirement_id"]
    ),
    "extract_cost_items": _object_schema({"evidence_ids": ID_LIST}, ["evidence_ids"]),
    "normalize_cost": _object_schema(
        {
            "amount": NUMBER,
            "period": {"type": "string", "enum": ["one_time", "monthly", "annual"]},
        },
        ["amount", "period"],
    ),
    "calculate_tco": _object_schema(
        {
            "one_time_cost": NUMBER,
            "annual_recurring_cost": NUMBER,
            "optional_cost": NUMBER,
            "evidence_ids": ID_LIST,
        },
        ["one_time_cost", "annual_recurring_cost", "optional_cost", "evidence_ids"],
    ),
    "compare_cost_items": _object_schema(
        {"items": {"type": "array", "items": {"type": "object"}}}, ["items"]
    ),
    "extract_commitments": _object_schema({"evidence_ids": ID_LIST}, ["evidence_ids"]),
    "compare_milestones": _object_schema({"evidence_ids": ID_LIST}, ["evidence_ids"]),
    "check_dependencies": _object_schema({"evidence_ids": ID_LIST}, ["evidence_ids"]),
    "detect_commitment_conflicts": _object_schema({"evidence_ids": ID_LIST}, ["evidence_ids"]),
}


class ReviewToolRegistry:
    def __init__(self, input_data: VendorReviewInput):
        self.input_data = input_data
        self.authoritative_cost: dict[str, Any] | None = None
        self._handlers: dict[str, Callable[..., Any]] = {
            name: getattr(self, f"_{name}") for name in TOOL_SCHEMAS
        }

    @property
    def names(self) -> set[str]:
        return set(self._handlers)

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"VendorLens deterministic review tool: {name}",
                    "parameters": schema,
                },
            }
            for name, schema in TOOL_SCHEMAS.items()
        ]

    def call(self, name: str, **arguments: Any) -> Any:
        if name not in self._handlers:
            raise ReviewToolAccessError(f"tool {name!r} is not registered")
        try:
            return self._handlers[name](**arguments)
        except ReviewToolAccessError:
            raise
        except (TypeError, ValueError) as error:
            raise ReviewToolAccessError(f"invalid arguments for tool {name!r}") from error

    def _domain_evidence(self, domain: ReviewDomain) -> list[EvidenceItem]:
        return self.input_data.evidence_by_domain.get(domain, self.input_data.evidence)

    def _evidence(self, domain: ReviewDomain, evidence_ids: list[str]) -> list[EvidenceItem]:
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or not all(isinstance(item, str) for item in evidence_ids)
        ):
            raise ReviewToolAccessError("evidence_ids must be a non-empty string list")
        available = {item.id: item for item in self._domain_evidence(domain)}
        unknown = set(evidence_ids) - set(available)
        if unknown:
            raise ReviewToolAccessError(
                f"evidence is outside {domain.value}: {', '.join(sorted(unknown))}"
            )
        return [available[item_id] for item_id in evidence_ids]

    def _requirement(self, requirement_id: str):
        for requirement in self.input_data.requirements:
            if requirement.id == requirement_id:
                return requirement
        raise ReviewToolAccessError(f"unknown requirement: {requirement_id}")

    def _search_requirement_evidence(self, requirement_id: str, query: str) -> list[dict]:
        requirement = self._requirement(requirement_id)
        terms = set(tokenize(f"{requirement.text} {query}"))
        items = self._domain_evidence(ReviewDomain.REQUIREMENT_COMPLIANCE)
        ranked = sorted(
            items,
            key=lambda item: len(terms & set(tokenize(item.quote))),
            reverse=True,
        )
        return [{"evidence_id": item.id, "quote": item.quote} for item in ranked[:5]]

    def _compare_requirement_response(self, requirement_id: str, evidence_ids: list[str]) -> dict:
        requirement = self._requirement(requirement_id)
        items = self._evidence(ReviewDomain.REQUIREMENT_COMPLIANCE, evidence_ids)
        quote = " ".join(item.quote for item in items)
        negative = any(word in quote for word in ("不支持", "不满足", "无法", "不提供"))
        overlap = len(set(tokenize(requirement.text)) & set(tokenize(quote))) / max(
            len(set(tokenize(requirement.text))), 1
        )
        status = RequirementStatus.NOT_MET if negative else RequirementStatus.PARTIALLY_MET
        if not negative and overlap >= 0.2:
            status = RequirementStatus.MET
        return {"status": status.value, "overlap": overlap, "evidence_ids": evidence_ids}

    def _mark_missing_response(self, requirement_id: str) -> dict:
        self._requirement(requirement_id)
        return {"status": RequirementStatus.NOT_STATED.value, "evidence_ids": []}

    def _extract_cost_items(self, evidence_ids: list[str]) -> list[dict]:
        items = self._evidence(ReviewDomain.COST_MODELING, evidence_ids)
        extracted = []
        for item in items:
            for match in DeterministicCostEngine.amount_pattern.finditer(item.quote):
                extracted.append(
                    {
                        "amount": DeterministicCostEngine._amount(match),
                        "evidence_id": item.id,
                        "quote": item.quote,
                    }
                )
        return extracted

    def _normalize_cost(self, amount: float, period: str) -> dict:
        if amount < 0 or period not in {"one_time", "monthly", "annual"}:
            raise ValueError("invalid cost")
        annual = amount * 12 if period == "monthly" else amount
        return {"amount": amount, "period": period, "annual_amount": annual}

    def _calculate_tco(
        self,
        one_time_cost: float,
        annual_recurring_cost: float,
        optional_cost: float,
        evidence_ids: list[str],
    ) -> dict:
        self._evidence(ReviewDomain.COST_MODELING, evidence_ids)
        if min(one_time_cost, annual_recurring_cost, optional_cost) < 0:
            raise ValueError("cost cannot be negative")
        result = {
            "vendor_id": self.input_data.vendor_id,
            "currency": self.input_data.currency,
            "comparison_months": self.input_data.comparison_months,
            "one_time_cost": one_time_cost,
            "recurring_cost": annual_recurring_cost,
            "optional_cost": optional_cost,
            "total_cost_of_ownership": one_time_cost
            + annual_recurring_cost * self.input_data.comparison_months / 12,
            "evidence_ids": evidence_ids,
        }
        self.authoritative_cost = result
        return result

    def _compare_cost_items(self, items: list[dict]) -> dict:
        fingerprints = [repr(sorted(item.items())) for item in items]
        return {"duplicate_count": len(fingerprints) - len(set(fingerprints))}

    def _extract_commitments(self, evidence_ids: list[str]) -> list[dict]:
        return self._commitment_quotes(evidence_ids)

    def _compare_milestones(self, evidence_ids: list[str]) -> dict:
        quotes = self._commitment_quotes(evidence_ids)
        numbers = sorted({value for item in quotes for value in re.findall(r"\d+", item["quote"])})
        return {"values": numbers, "conflicting": len(numbers) > 1}

    def _check_dependencies(self, evidence_ids: list[str]) -> dict:
        quotes = self._commitment_quotes(evidence_ids)
        dependencies = [
            item
            for item in quotes
            if any(word in item["quote"] for word in ("依赖", "前提", "甲方"))
        ]
        return {"dependencies": dependencies}

    def _detect_commitment_conflicts(self, evidence_ids: list[str]) -> dict:
        return self._compare_milestones(evidence_ids)

    def _commitment_quotes(self, evidence_ids: list[str]) -> list[dict]:
        items = self._evidence(ReviewDomain.COMMITMENT_VERIFICATION, evidence_ids)
        return [{"evidence_id": item.id, "quote": item.quote} for item in items]


assert set(TOOL_SCHEMAS) == set().union(*REVIEW_TOOLS_BY_DOMAIN.values())
