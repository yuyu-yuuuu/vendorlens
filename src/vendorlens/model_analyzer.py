from __future__ import annotations

import json
from contextlib import nullcontext
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
from vendorlens.review_tools import TOOL_SCHEMAS, ReviewToolRegistry
from vendorlens.schemas import ReviewDomain
from vendorlens.tracing import get_active_trace


class ModelToolLoopError(ModelProtocolError):
    pass


class VendorReviewModelAnalyzer:
    def __init__(self, chat_model: ChatModel, *, max_tool_rounds: int = 4):
        if max_tool_rounds < 0:
            raise ValueError("max tool rounds cannot be negative")
        self.chat_model = chat_model
        self.max_tool_rounds = max_tool_rounds
        self.tracer = get_active_trace()

    def __call__(self, input_data: VendorReviewInput, _tools: Any) -> VendorReviewDraft:
        registry = ReviewToolRegistry(input_data)
        messages = self._messages(input_data)
        tool_rounds = 0
        while True:
            scope = (
                self.tracer.span("chat_completion", "model", round=tool_rounds + 1)
                if self.tracer
                else nullcontext()
            )
            with scope as span:
                response = self.chat_model.complete(
                    messages=messages,
                    tools=registry.definitions(),
                    tool_choice=self._tool_choice(input_data, registry),
                )
                if span:
                    span.attributes.update(
                        tool_call_count=len(response.tool_calls),
                        usage_reported=response.usage_reported,
                    )
                    if response.model:
                        span.attributes["model"] = response.model
                    if response.usage_reported:
                        span.attributes.update(response.usage.model_dump())
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
                    scope = (
                        self.tracer.span(
                            call.name if call.name in TOOL_SCHEMAS else "unregistered_tool",
                            "tool",
                            argument_count=len(call.arguments),
                            round=tool_rounds + 1,
                        )
                        if self.tracer
                        else nullcontext()
                    )
                    with scope:
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
    def _tool_choice(
        input_data: VendorReviewInput,
        registry: ReviewToolRegistry,
    ) -> str | dict[str, Any]:
        cost_is_active = not input_data.revision_domains or ReviewDomain.COST_MODELING in (
            input_data.revision_domains
        )
        if cost_is_active and registry.authoritative_cost is None:
            return {
                "type": "function",
                "function": {"name": "calculate_tco"},
            }
        return "auto"

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
        draft_schema = json.dumps(
            VendorReviewDraft.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        instruction = (
            "你是 VendorLens 的单一供应商评审 Agent。只使用输入证据和已注册工具；"
            "所有事实必须引用 evidence_id；成本 TCO 必须采用 calculate_tco 的结果；"
            "不得选择最终供应商。最终只返回符合以下 JSON Schema 的 JSON："
            f"{draft_schema}"
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
    store: bool | None = False
    request_options = None
    if settings.model_provider == "deepseek":
        store = None
        request_options = {
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
    chat_model = OpenAICompatibleChatModel(
        base_url=settings.model_base_url,
        api_key=settings.model_api_key.get_secret_value(),
        model=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
        store=store,
        request_options=request_options,
        client=client,
    )
    analyzer = VendorReviewModelAnalyzer(
        chat_model,
        max_tool_rounds=settings.model_max_tool_rounds,
    )
    return VendorReviewExecutor(VendorReviewAgent(analyzer))
