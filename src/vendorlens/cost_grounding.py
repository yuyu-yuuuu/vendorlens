"""Shared fee provenance and arithmetic for tools, output validation and fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from vendorlens.numeric_facts import decimal_value, money_mentions
from vendorlens.schemas import CostSourceItem, EvidenceItem

COST_CATEGORY_MARKERS = {
    "software": ("软件", "许可", "授权", "license"),
    "implementation": ("实施", "部署", "迁移", "集成"),
    "maintenance": ("维护", "维保", "运维"),
    "training": ("培训",),
    "hardware": ("硬件", "服务器", "设备"),
    "hosting": ("云服务", "托管", "主机"),
    "support": ("支持服务", "技术支持"),
}


class CostGroundingError(ValueError):
    pass


def evidence_text(item: EvidenceItem) -> str:
    # EvidenceItem validates hash and containment when parent context is supplied.
    return item.context_text if item.context_text is not None else item.quote


@dataclass
class GroundedCosts:
    items: list[CostSourceItem]
    missing: list[str]

    def require_categories(self, required):
        present = {
            category
            for item in self.items
            for category in item.business_categories
            if item.category != "optional"
        }
        self.missing = list(
            dict.fromkeys(
                self.missing
                + [
                    f"必要费用类别未提供可核验报价: {category}"
                    for category in required
                    if category not in present
                ]
            )
        )
        return self

    def totals(self) -> dict[str, Decimal]:
        totals = dict.fromkeys(("one_time", "recurring", "optional"), Decimal(0))
        for item in self.items:
            value = decimal_value(item.amount)
            if item.category == "recurring" and item.period == "monthly":
                value *= 12
            totals[item.category] += value
        return totals

    def result(self, *, vendor_id: str, currency: str, comparison_months: int) -> dict:
        totals = self.totals()
        return {
            "vendor_id": vendor_id,
            "currency": currency,
            "comparison_months": comparison_months,
            "one_time_cost": float(totals["one_time"]),
            "recurring_cost": float(totals["recurring"]),
            "optional_cost": float(totals["optional"]),
            "total_cost_of_ownership": float(
                totals["one_time"] + totals["recurring"] * comparison_months / 12
            ),
            "cost_sources": [item.model_dump() for item in self.items],
            "evidence_ids": list(dict.fromkeys(item.evidence_id for item in self.items)),
        }


def extract_grounded_costs(evidence: list[EvidenceItem], currency: str) -> GroundedCosts:
    items: list[CostSourceItem] = []
    missing: list[str] = []
    seen: set[tuple] = set()
    for evidence_item in evidence:
        text = evidence_text(evidence_item)
        # Clause splitting preserves decimals and thousands separators in amounts.
        for clause_match in re.finditer(r"(?:[^，；;。\n,]|(?<=\d),(?=\d))+", text):
            clause = clause_match.group()
            mentions = money_mentions(clause)
            if not mentions:
                continue
            key = (
                evidence_item.document_version_id,
                evidence_item.chunk_id,
                clause_match.start(),
                clause_match.end(),
            )
            if key in seen:
                continue
            seen.add(key)
            reason = None
            if re.search(r"\d+(?:\.\d+)?\s*(?:[eE][+-]?\d+|/\d+)", clause):
                reason = "科学计数或分数金额尚不支持"
            elif len(mentions) != 1:
                reason = "同一费用片段包含多个金额，无法确定费用关系"
            elif mentions[0].fact.unit != currency:
                reason = "币种不一致或不明确，不自动换汇"
            elif mentions[0].fact.value < 0:
                reason = "负金额或折扣需要明确费用关系"
            elif mentions[0].fact.period == "ambiguous":
                reason = "费用周期不明确"
            elif re.search(
                r"合计|总计|总报价|总价|小计|含税总|未税|不含税|另计|起价|起步|约|预计|至|~|～|折扣|"
                r"单价|每人|每台|每套|/人|/台|/套|每季度|季度|/季|每天|每日|/天|每周|/周|"
                r"已包含|已含|含年度|含每年|免收|不收费|另收费|另付|不含维保",
                clause,
            ):
                reason = "汇总、估算、税费或计价单位需要进一步确认"
            elif re.search(r"(?<!\d)\d+\s*(?:年|个月|月)\s*(?:费用|费|总|报价)", clause):
                reason = "多年或多月打包费用无法按年直接归一化"
            if reason:
                missing.append(reason)
                continue
            mention = mentions[0]
            optional = any(marker in clause for marker in ("可选", "选配", "额外购买"))
            if optional and mention.fact.period != "one_time":
                missing.append("周期性可选费用尚不支持")
                continue
            period = mention.fact.period
            category = (
                "optional" if optional else ("recurring" if period != "one_time" else "one_time")
            )
            value = mention.fact.value
            items.append(
                CostSourceItem(
                    evidence_id=evidence_item.id,
                    source_text=clause.strip(),
                    start=clause_match.start() + len(clause) - len(clause.lstrip()),
                    end=clause_match.end() - (len(clause) - len(clause.rstrip())),
                    amount=float(value),
                    currency=currency,
                    period=period,
                    category=category,
                    annual_amount=float(value * 12 if period == "monthly" else value),
                    business_categories=[
                        category
                        for category, markers in COST_CATEGORY_MARKERS.items()
                        if any(marker in clause.lower() for marker in markers)
                    ],
                )
            )
    if not items:
        missing.append("未找到可确定币种和周期的明确费用项")
    return GroundedCosts(items, list(dict.fromkeys(missing)))


def validate_cost_numbers(
    grounded: GroundedCosts,
    *,
    one_time: float,
    recurring: float,
    optional: float,
    tco: float | None = None,
    comparison_months: int = 36,
) -> None:
    if not grounded.items:
        raise CostGroundingError("cost_source_missing")
    totals = grounded.totals()
    for field, value in (("one_time", one_time), ("recurring", recurring), ("optional", optional)):
        if decimal_value(value) != totals[field]:
            raise CostGroundingError(f"cost_input_mismatch:{field}")
    if tco is not None:
        expected = totals["one_time"] + totals["recurring"] * comparison_months / 12
        # Transport uses floats; permit only sub-cent serialization noise.
        if abs(decimal_value(tco) - expected) > Decimal("0.000001"):
            raise CostGroundingError("derived_tco_mismatch")
