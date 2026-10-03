"""Conservative scope rules; lexical checks are not general semantic entailment."""

import re

from vendorlens.retrieval import tokenize
from vendorlens.schemas import RequirementStatus

LIMITATIONS = (
    "仅限",
    "仅支持",
    "需另行",
    "另行购买",
    "前提",
    "取决于",
    "待确认",
    "尚需",
    "部分支持",
    "上限",
    "另付费",
    "必须联网",
    "只能保留",
)


def requirement_response(requirement: str, text: str):
    subject = re.sub(r"系统|必须|应当|需要|支持|满足|提供|具备|是否|能够|要求", "", requirement)
    terms = set(tokenize(subject)) or set(tokenize(requirement))
    clauses = [c.strip() for c in re.split(r"[。；;\n]|但是|但|然而", text) if c.strip()]
    relevant = [c for c in clauses if len(terms & set(tokenize(c))) / max(len(terms), 1) >= 0.2]
    if not relevant:
        return {
            "status": RequirementStatus.NOT_STATED.value,
            "supporting_text": [],
            "limitations": [],
        }
    # Buyer requirements and unanswered headings are not supplier capability claims.
    relevant = [
        c
        for c in relevant
        if not re.search(r"待确认|未作答|未提供|未.*描述|缺少附件|进一步沟通|需.*实测", c)
        and not (
            re.search(r"采购方要求|采购方列出|客户需求书|必须|应当", c)
            and not re.search(r"供应商响应|但|仅|不支持", c)
        )
    ]
    if not relevant:
        return {
            "status": RequirementStatus.NOT_STATED.value,
            "supporting_text": [],
            "limitations": [],
        }
    future = any(re.search(r"计划.*发布|下一年度|当前版本只能", c) for c in relevant)
    negative = any(
        re.search(r"不支持|无法|不满足|不提供|不具备|不能|不可用|没有该功能", c) for c in relevant
    )
    positive = any(
        re.search(r"(?<!不)支持|(?<!不)满足|符合|(?<!不)提供|(?<!不)具备", c)
        and not re.search(r"必须|应当|要求|需支持", c)
        for c in relevant
    )
    restrictions = [c for c in clauses if any(marker in c for marker in LIMITATIONS)]
    # A requirement naming an edition must not inherit support from another edition.
    edition_denied = any(
        edition in requirement and re.search(rf"{edition}.*(?:没有|不支持|不可用)", text)
        for edition in ("标准版", "企业版", "基础版")
    )
    upper_bound = re.search(r"不超过\s*(\d+(?:\.\d+)?)\s*(秒|毫秒)", requirement)
    measured = re.search(r"(?:为|达到)\s*(\d+(?:\.\d+)?)\s*(秒|毫秒)", text)
    if edition_denied:
        status = RequirementStatus.NOT_MET
    elif upper_bound and measured and upper_bound[2] == measured[2]:
        status = (
            RequirementStatus.MET
            if float(measured[1]) <= float(upper_bound[1])
            else RequirementStatus.NOT_MET
        )
    elif future:
        status = RequirementStatus.PARTIALLY_MET
    elif negative:
        status = RequirementStatus.PARTIALLY_MET if positive else RequirementStatus.NOT_MET
    elif restrictions and positive:
        status = RequirementStatus.PARTIALLY_MET
    elif not positive:
        status = RequirementStatus.NOT_STATED
    else:
        status = RequirementStatus.MET
    return {"status": status.value, "supporting_text": relevant, "limitations": restrictions}


def milestone_values(text: str):
    """Compare like units, rather than treating all numbers as delivery promises."""
    return {
        (unit, value)
        for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(工作日|个月|天|周|月|小时)", text)
    }
