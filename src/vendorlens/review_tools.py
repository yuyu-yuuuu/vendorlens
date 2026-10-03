from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vendorlens.cost_grounding import (
    CostGroundingError,
    extract_grounded_costs,
    validate_cost_numbers,
)
from vendorlens.numeric_facts import decimal_value
from vendorlens.retrieval import tokenize
from vendorlens.review_agent import (
    REVIEW_TOOLS_BY_DOMAIN,
    ReviewToolAccessError,
    ReviewToolGroundingError,
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
        except CostGroundingError as error:
            raise ReviewToolGroundingError(str(error)) from error
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
            key=lambda item: len(terms & set(tokenize(item.context_text or item.quote))),
            reverse=True,
        )
        return [
            {"evidence_id": item.id, "quote": item.quote, "context_text": item.context_text}
            for item in ranked[:5]
        ]

    def _compare_requirement_response(self, requirement_id: str, evidence_ids: list[str]) -> dict:
        requirement = self._requirement(requirement_id)
        items = self._evidence(ReviewDomain.REQUIREMENT_COMPLIANCE, evidence_ids)
        from vendorlens.semantic_rules import requirement_response

        text = " ".join(item.context_text or item.quote for item in items)
        return {**requirement_response(requirement.text, text), "evidence_ids": evidence_ids}

    def _mark_missing_response(self, requirement_id: str) -> dict:
        self._requirement(requirement_id)
        return {"status": RequirementStatus.NOT_STATED.value, "evidence_ids": []}

    def _extract_cost_items(self, evidence_ids: list[str]) -> list[dict]:
        items = self._evidence(ReviewDomain.COST_MODELING, evidence_ids)
        grounded = extract_grounded_costs(items, self.input_data.currency).require_categories(
            self.input_data.required_cost_categories
        )
        return [item.model_dump() for item in grounded.items]

    def _normalize_cost(self, amount: float, period: str) -> dict:
        value = decimal_value(amount)
        if value < 0 or period not in {"one_time", "monthly", "annual"}:
            raise ValueError("invalid cost")
        annual = value * 12 if period == "monthly" else value
        # This is an arithmetic helper, not evidence authorization for calculate_tco.
        return {"amount": amount, "period": period, "annual_amount": float(annual)}

    def _calculate_tco(
        self,
        one_time_cost: float,
        annual_recurring_cost: float,
        optional_cost: float,
        evidence_ids: list[str],
    ) -> dict:
        self.authoritative_cost = None
        items = self._evidence(ReviewDomain.COST_MODELING, evidence_ids)
        grounded = extract_grounded_costs(items, self.input_data.currency).require_categories(
            self.input_data.required_cost_categories
        )
        validate_cost_numbers(
            grounded,
            one_time=one_time_cost,
            recurring=annual_recurring_cost,
            optional=optional_cost,
        )
        result = grounded.result(
            vendor_id=self.input_data.vendor_id,
            currency=self.input_data.currency,
            comparison_months=self.input_data.comparison_months,
        )
        result["missing_costs"] = grounded.missing
        self.authoritative_cost = result
        return result

    def _compare_cost_items(self, items: list[dict]) -> dict:
        fingerprints = [repr(sorted(item.items())) for item in items]
        return {"duplicate_count": len(fingerprints) - len(set(fingerprints))}

    def _extract_commitments(self, evidence_ids: list[str]) -> list[dict]:
        return self._commitment_quotes(evidence_ids)

    def _compare_milestones(self, evidence_ids: list[str]) -> dict:
        quotes = self._commitment_quotes(evidence_ids)
        from vendorlens.semantic_rules import milestone_values

        values = set().union(*(milestone_values(item["context_text"]) for item in quotes))
        units = {unit for unit, _ in values}
        return {
            "values": sorted({value for _, value in values}),
            "conflicting": any(len({v for u, v in values if u == unit}) > 1 for unit in units),
        }

    def _check_dependencies(self, evidence_ids: list[str]) -> dict:
        quotes = self._commitment_quotes(evidence_ids)
        dependencies = [
            item
            for item in quotes
            if any(word in item["context_text"] for word in ("依赖", "前提", "甲方", "客户"))
        ]
        return {"dependencies": dependencies}

    def _detect_commitment_conflicts(self, evidence_ids: list[str]) -> dict:
        return self._compare_milestones(evidence_ids)

    def _commitment_quotes(self, evidence_ids: list[str]) -> list[dict]:
        items = self._evidence(ReviewDomain.COMMITMENT_VERIFICATION, evidence_ids)
        return [
            {
                "evidence_id": item.id,
                "quote": item.quote,
                "context_text": item.context_text or item.quote,
            }
            for item in items
        ]


assert set(TOOL_SCHEMAS) == set().union(*REVIEW_TOOLS_BY_DOMAIN.values())
