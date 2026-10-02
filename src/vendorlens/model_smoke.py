from __future__ import annotations

import hashlib
import sys
from typing import Protocol

from vendorlens.config import get_settings
from vendorlens.model_adapter import ModelError
from vendorlens.model_analyzer import build_vendor_review_executor
from vendorlens.review_agent import ReviewAgentError, VendorReviewInput
from vendorlens.schemas import EvidenceItem, RequirementItem, ReviewDomain, VendorAssessment


class ReviewExecutor(Protocol):
    def execute(self, input_data: VendorReviewInput) -> VendorAssessment: ...


def _evidence(item_id: str, quote: str, page: int) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        project_id="model-smoke-project",
        vendor_id="smoke-vendor",
        document_version_id="model-smoke-version",
        chunk_id=f"chunk-{item_id}",
        quote=quote,
        source_text_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        location={"page": page},
    )


def build_smoke_input() -> VendorReviewInput:
    requirement = _evidence(
        "smoke-requirement-evidence",
        "供应商明确支持私有化部署，应用和数据库均可部署在客户自有环境。",
        1,
    )
    cost = _evidence(
        "smoke-cost-evidence",
        "软件许可及实施一次性费用 80 万元，年度维保服务费 10 万元。",
        2,
    )
    commitment = _evidence(
        "smoke-commitment-evidence",
        "合同签署后 90 天完成上线；客户需在项目启动前提供服务器和接口文档。",
        3,
    )
    return VendorReviewInput(
        task_id="model-smoke-task",
        project_id="model-smoke-project",
        vendor_id="smoke-vendor",
        document_version_ids=["model-smoke-version"],
        evidence=[requirement, cost, commitment],
        evidence_by_domain={
            ReviewDomain.REQUIREMENT_COMPLIANCE: [requirement],
            ReviewDomain.COST_MODELING: [cost],
            ReviewDomain.COMMITMENT_VERIFICATION: [commitment],
        },
        requirements=[
            RequirementItem(
                id="requirement-private-deployment",
                text="系统必须支持私有化部署",
                mandatory=True,
            )
        ],
        currency="CNY",
        comparison_months=36,
        required_cost_categories=["software", "implementation", "maintenance"],
        required_commitments=["delivery", "dependency"],
        project_context="验证 VendorLens 真实模型与工具调用链，不用于生产决策。",
    )


def run(executor: ReviewExecutor) -> VendorAssessment:
    return executor.execute(build_smoke_input())


def format_result(result: VendorAssessment) -> str:
    return result.model_dump_json(indent=2)


def main() -> None:
    try:
        executor = build_vendor_review_executor(get_settings())
        result = run(executor)
    except (ModelError, ReviewAgentError) as error:
        print(f"VendorLens model smoke test failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    print(format_result(result))


if __name__ == "__main__":
    main()
