import json

import pytest
from pydantic import SecretStr

from vendorlens.config import Settings
from vendorlens.model_adapter import (
    ModelConfigurationError,
    ModelProtocolError,
    ModelResponse,
    ModelToolCall,
)
from vendorlens.model_analyzer import (
    ModelToolLoopError,
    VendorReviewModelAnalyzer,
    build_vendor_review_executor,
)
from vendorlens.review_agent import VendorReviewInput
from vendorlens.schemas import EvidenceItem, RequirementItem, ReviewDomain


def evidence(item_id, quote):
    return EvidenceItem(
        id=item_id,
        project_id="project-1",
        document_version_id="version-1",
        chunk_id=f"chunk-{item_id}",
        quote=quote,
        source_text_hash="a" * 64,
        location={"page": 1},
        vendor_id="vendor-a",
    )


def review_input():
    req = evidence("req-ev", "支持私有化部署")
    cost = evidence("cost-ev", "实施费用 80 万元")
    commit = evidence("commit-ev", "90 天交付")
    return VendorReviewInput(
        task_id="task-1",
        project_id="project-1",
        vendor_id="vendor-a",
        document_version_ids=["version-1"],
        evidence=[req, cost, commit],
        evidence_by_domain={
            ReviewDomain.REQUIREMENT_COMPLIANCE: [req],
            ReviewDomain.COST_MODELING: [cost],
            ReviewDomain.COMMITMENT_VERIFICATION: [commit],
        },
        requirements=[RequirementItem(id="req-1", text="支持私有化部署", mandatory=True)],
        currency="CNY",
        comparison_months=36,
        required_commitments=["delivery"],
    )


def final_draft(total=1):
    return {
        "requirement_findings": [
            {
                "vendor_id": "vendor-a",
                "requirement_id": "req-1",
                "mandatory": True,
                "status": "met",
                "explanation": "材料明确支持",
                "evidence_ids": ["req-ev"],
                "confidence": 0.9,
            }
        ],
        "cost_assessment": {
            "vendor_id": "vendor-a",
            "currency": "CNY",
            "comparison_months": 36,
            "one_time_cost": 1,
            "recurring_cost": 0,
            "optional_cost": 0,
            "total_cost_of_ownership": total,
            "evidence_ids": ["cost-ev"],
        },
        "commitment_assessment": {
            "vendor_id": "vendor-a",
            "findings": [
                {
                    "category": "delivery",
                    "statement": "90 天交付",
                    "status": "verified",
                    "evidence_ids": ["commit-ev"],
                }
            ],
        },
    }


class SequenceModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, *, messages, tools=None, tool_choice=None):
        self.requests.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        return next(self.responses)


def test_analyzer_executes_tools_and_enforces_tco_result():
    chat_model = SequenceModel(
        [
            ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        id="call-1",
                        name="calculate_tco",
                        arguments={
                            "one_time_cost": 800_000,
                            "annual_recurring_cost": 100_000,
                            "optional_cost": 50_000,
                            "evidence_ids": ["cost-ev"],
                        },
                    )
                ]
            ),
            ModelResponse(content=json.dumps(final_draft(total=1))),
        ]
    )

    draft = VendorReviewModelAnalyzer(chat_model)(review_input(), None)

    assert draft.cost_assessment.total_cost_of_ownership == 1_100_000
    assert draft.cost_assessment.one_time_cost == 800_000
    assert chat_model.requests[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "calculate_tco"},
    }
    assert chat_model.requests[1]["tool_choice"] == "auto"
    assert chat_model.requests[1]["messages"][-1]["tool_call_id"] == "call-1"


def test_analyzer_marks_revision_domains_in_prompt():
    input_data = review_input().model_copy(
        update={"revision_domains": {ReviewDomain.REQUIREMENT_COMPLIANCE}, "revision_count": 1}
    )
    chat_model = SequenceModel([ModelResponse(content=json.dumps(final_draft()))])

    VendorReviewModelAnalyzer(chat_model)(input_data, None)

    prompt = chat_model.requests[0]["messages"][1]["content"]
    assert "requirement_compliance" in prompt
    assert "返工次数：1" in prompt
    assert "requirement_findings" in chat_model.requests[0]["messages"][0]["content"]


def test_analyzer_rejects_cost_result_without_authoritative_tco_tool():
    chat_model = SequenceModel([ModelResponse(content=json.dumps(final_draft()))])

    with pytest.raises(ModelProtocolError, match="calculate_tco"):
        VendorReviewModelAnalyzer(chat_model)(review_input(), None)


def test_analyzer_stops_infinite_tool_loop():
    response = ModelResponse(
        tool_calls=[
            ModelToolCall(
                id="call-1", name="mark_missing_response", arguments={"requirement_id": "req-1"}
            )
        ]
    )
    chat_model = SequenceModel([response, response])

    with pytest.raises(ModelToolLoopError):
        VendorReviewModelAnalyzer(chat_model, max_tool_rounds=1)(review_input(), None)


def test_executor_factory_requires_complete_model_configuration():
    with pytest.raises(ModelConfigurationError, match="model configuration"):
        build_vendor_review_executor(Settings(_env_file=None))


def test_executor_factory_does_not_expose_api_key():
    settings = Settings(
        _env_file=None,
        model_base_url="https://models.example/v1",
        model_api_key=SecretStr("secret-value"),
        model_name="compatible-model",
    )

    executor = build_vendor_review_executor(settings)

    assert "secret-value" not in repr(executor.agent._analyzer.chat_model)


def test_executor_factory_configures_deepseek_non_thinking_mode():
    settings = Settings(
        _env_file=None,
        model_provider="deepseek",
        model_base_url="https://api.deepseek.com",
        model_api_key=SecretStr("secret-value"),
        model_name="deepseek-flash",
    )

    executor = build_vendor_review_executor(settings)
    chat_model = executor.agent._analyzer.chat_model

    assert chat_model.store is None
    assert chat_model.request_options == {
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
