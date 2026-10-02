from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from vendorlens.config import Settings
from vendorlens.model_adapter import (
    ChatModel,
    ModelConfigurationError,
    ModelProtocolError,
    OpenAICompatibleChatModel,
)
from vendorlens.review_agent import (
    VendorReviewAgent,
    VendorReviewDraft,
    VendorReviewExecutor,
    VendorReviewInput,
)
from vendorlens.review_tools import ReviewToolRegistry
from vendorlens.schemas import ReviewDomain


class ModelToolLoopError(ModelProtocolError):
    pass


class VendorReviewModelAnalyzer:
    def __init__(self, chat_model: ChatModel, *, max_tool_rounds: int = 4):
        if max_tool_rounds < 0:
            raise ValueError("max tool rounds cannot be negative")
        self.chat_model = chat_model
        self.max_tool_rounds = max_tool_rounds

    def __call__(self, input_data: VendorReviewInput, _tools: Any) -> VendorReviewDraft:
        registry = ReviewToolRegistry(input_data)
        messages = self._messages(input_data)
        tool_rounds = 0
        while True:
            response = self.chat_model.complete(messages=messages, tools=registry.definitions())
            if response.tool_calls:
                if tool_rounds >= self.max_tool_rounds:
                    raise ModelToolLoopError("model exceeded the tool call round limit")
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                                },
                            }
                            for call in response.tool_calls
                        ],
                    }
                )
                for call in response.tool_calls:
                    result = registry.call(call.name, **call.arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(
                                result, ensure_ascii=False, separators=(",", ":")
                            ),
                        }
                    )
                tool_rounds += 1
                continue
            if not response.content:
                raise ModelProtocolError("model returned no final review content")
            draft = self._parse_draft(response.content)
            return self._apply_authoritative_results(input_data, draft, registry)

    @staticmethod
    def _messages(input_data: VendorReviewInput) -> list[dict[str, str]]:
        revision = ", ".join(sorted(domain.value for domain in input_data.revision_domains))
        payload = {
            "task_id": input_data.task_id,
            "project_id": input_data.project_id,
            "vendor_id": input_data.vendor_id,
            "requirements": [item.model_dump(mode="json") for item in input_data.requirements],
            "currency": input_data.currency,
            "comparison_months": input_data.comparison_months,
            "required_cost_categories": input_data.required_cost_categories,
            "required_commitments": input_data.required_commitments,
            "project_context": input_data.project_context,
            "evidence": [item.model_dump(mode="json") for item in input_data.evidence],
        }
        instruction = (
            "你是 VendorLens 的单一供应商评审 Agent。只使用输入证据和已注册工具；"
            "所有事实必须引用 evidence_id；成本 TCO 必须采用 calculate_tco 的结果；"
            "不得选择最终供应商。最终只返回符合 VendorReviewDraft schema 的 JSON。"
        )
        user = (
            f"返工领域：{revision or '全部领域'}\n返工次数：{input_data.revision_count}\n"
            f"输入：{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        return [{"role": "system", "content": instruction}, {"role": "user", "content": user}]

    @staticmethod
    def _parse_draft(content: str) -> VendorReviewDraft:
        try:
            payload = json.loads(content)
            return VendorReviewDraft.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as error:
            raise ModelProtocolError("model returned an invalid VendorReviewDraft") from error

    @staticmethod
    def _apply_authoritative_results(
        input_data: VendorReviewInput,
        draft: VendorReviewDraft,
        registry: ReviewToolRegistry,
    ) -> VendorReviewDraft:
        cost_is_active = not input_data.revision_domains or ReviewDomain.COST_MODELING in (
            input_data.revision_domains
        )
        if draft.cost_assessment is None or not cost_is_active:
            return draft
        if registry.authoritative_cost is None:
            raise ModelProtocolError("cost assessment requires calculate_tco tool output")
        cost = draft.cost_assessment.model_copy(update=registry.authoritative_cost)
        return draft.model_copy(update={"cost_assessment": cost})


def build_vendor_review_executor(
    settings: Settings,
    *,
    client: httpx.Client | None = None,
) -> VendorReviewExecutor:
    if not settings.model_base_url or not settings.model_api_key or not settings.model_name:
        raise ModelConfigurationError("complete model configuration is required")
    chat_model = OpenAICompatibleChatModel(
        base_url=settings.model_base_url,
        api_key=settings.model_api_key.get_secret_value(),
        model=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
        client=client,
    )
    analyzer = VendorReviewModelAnalyzer(
        chat_model,
        max_tool_rounds=settings.model_max_tool_rounds,
    )
    return VendorReviewExecutor(VendorReviewAgent(analyzer))
