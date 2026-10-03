"""Conservative, dimension-aware numeric matching; no language-model judgments."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

NUMBER = r"[+-]?[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
DATE_PATTERN = re.compile(r"(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?!\d)")
MONEY_PATTERN = re.compile(
    rf"(?<![\d.])(?P<prefix>CNY|RMB|USD|EUR|人民币|美元|欧元|[¥￥$€])?\s*"
    rf"(?P<number>{NUMBER})\s*(?P<scale>亿|万|千)?\s*"
    r"(?P<suffix>人民币|美元|欧元|元|CNY|RMB|USD|EUR)?",
    re.IGNORECASE,
)
CURRENCIES = {
    "CNY": "CNY", "RMB": "CNY", "人民币": "CNY", "元": "CNY", "¥": "CNY", "￥": "CNY",
    "USD": "USD", "美元": "USD", "$": "USD", "EUR": "EUR", "欧元": "EUR", "€": "EUR",
}
SCALES = {None: Decimal(1), "千": Decimal(1000), "万": Decimal(10000), "亿": Decimal(100000000)}
UNIT_PATTERN = re.compile(
    rf"(?<![\d.])(?P<number>{NUMBER})\s*"
    r"(?P<unit>个百分点|%|％|工作日|人天|个月|分钟|小时|秒钟|毫秒|年|月|周|天|日|秒|人|个|次|台)?"
)


@dataclass(frozen=True)
class NumericFact:
    kind: str
    value: Decimal | str
    unit: str
    period: str = ""


@dataclass(frozen=True)
class MoneyMention:
    fact: NumericFact
    start: int
    end: int
    raw: str


def decimal_value(value: str | float | int) -> Decimal:
    result = Decimal(str(value).replace(",", ""))
    if not result.is_finite():
        raise ValueError("numeric values must be finite")
    return result


def cost_period(text: str) -> str:
    monthly = bool(re.search(r"每月|月费|月度|/月|／月|按月", text))
    annual = bool(re.search(r"每年|年费|年度|年化|/年|／年|按年", text))
    if monthly and annual:
        return "ambiguous"
    if monthly:
        return "monthly"
    if annual:
        return "annual"
    return "one_time"


def money_mentions(text: str) -> list[MoneyMention]:
    mentions = []
    for match in MONEY_PATTERN.finditer(text):
        prefix, suffix = match["prefix"], match["suffix"]
        if not prefix and not suffix:
            continue
        currencies = {CURRENCIES[value.upper()] for value in (prefix, suffix) if value}
        currency = next(iter(currencies)) if len(currencies) == 1 else "ambiguous"
        # Period is local to the amount's clause, never borrowed from a different fee.
        start = max(
            text.rfind(mark, 0, match.start()) for mark in ("，", ",", "；", ";", "。", "\n")
        )
        ends = [text.find(mark, match.end()) for mark in ("，", ",", "；", ";", "。", "\n")]
        end = min((value for value in ends if value >= 0), default=len(text))
        clause = text[start + 1:end]
        mentions.append(MoneyMention(
            NumericFact("amount", decimal_value(match["number"]) * SCALES[match["scale"]],
                        currency, cost_period(clause)),
            match.start(), match.end(), match.group().strip(),
        ))
    return mentions



_CHINESE_DIGITS = {char: index for index, char in enumerate("零一二三四五六七八九")}
_CHINESE_DIGITS.update({"〇": 0, "两": 2})


def _chinese_number(text: str) -> str:
    if all(char in _CHINESE_DIGITS for char in text):
        return "".join(str(_CHINESE_DIGITS[char]) for char in text)
    total = section = number = 0
    for char in text:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
        elif char in {"十", "百", "千"}:
            section += (number or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            number = 0
        else:
            section += number
            total += section * {"万": 10000, "亿": 100000000}[char]
            section = number = 0
    return str(total + section + number)


def normalize_chinese_numbers(text: str) -> str:
    # Only numeric expressions preceding recognized units; do not rewrite ordinary words.
    text = re.sub(
        r"百分之([零〇一二两三四五六七八九十百千]+)",
        lambda match: _chinese_number(match[1]) + "%", text,
    )
    return re.sub(
        r"(?<![0-9零〇一二两三四五六七八九十百千万亿])[零〇一二两三四五六七八九十百][零〇一二两三四五六七八九十百千万亿]*(?=\s*"
        r"(?:元|美元|欧元|%|％|工作日|人天|个月|分钟|小时|秒钟|毫秒|年|月|周|天|日|秒|人|个|次|台))",
        lambda match: _chinese_number(match.group()), text,
    )

def numeric_facts(text: str) -> set[NumericFact]:
    text = normalize_chinese_numbers(text)
    facts: set[NumericFact] = set()
    masked = list(text)
    for match in DATE_PATTERN.finditer(text):
        try:
            value = date(*map(int, match.groups())).isoformat()
        except ValueError:
            value = "invalid:" + match.group()
        facts.add(NumericFact("date", value, "date"))
        masked[match.start():match.end()] = " " * (match.end() - match.start())
    # Unsupported scientific/fraction notation must not authorize its trailing digits.
    for match in re.finditer(r"\d+(?:\.\d+)?\s*(?:[eE][+-]?\d+|/\d+)", "".join(masked)):
        facts.add(NumericFact("unsupported", match.group(), "notation"))
        masked[match.start():match.end()] = " " * (match.end() - match.start())
    for mention in money_mentions("".join(masked)):
        facts.add(mention.fact)
        masked[mention.start:mention.end] = " " * (mention.end - mention.start)
    for match in UNIT_PATTERN.finditer("".join(masked)):
        value = decimal_value(match["number"])
        unit = match["unit"] or "number"
        if unit in {"%", "％"}:
            fact = NumericFact("percentage", value, "%")
        elif unit == "个百分点":
            fact = NumericFact("percentage_point", value, unit)
        elif unit in {"年", "月", "个月"}:
            fact = NumericFact("duration", value * (12 if unit == "年" else 1), "month")
        elif unit in {"周", "天", "日", "小时", "分钟", "秒", "秒钟", "毫秒"}:
            scale = {"周": 604800, "天": 86400, "日": 86400, "小时": 3600,
                     "分钟": 60, "秒": 1, "秒钟": 1, "毫秒": Decimal("0.001")}[unit]
            fact = NumericFact("duration", value * scale, "second")
        else:
            fact = NumericFact("number", value, unit)
        facts.add(fact)
    return facts


def unsupported_numeric_facts(text: str, sources: list[str]) -> set[NumericFact]:
    observed = set().union(*(numeric_facts(source) for source in sources))
    expected = numeric_facts(text)
    return {fact for fact in expected if fact not in observed
            or fact.kind == "unsupported"
            or (fact.kind == "date" and str(fact.value).startswith("invalid:"))}
