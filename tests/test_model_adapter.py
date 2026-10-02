import httpx
import pytest

from vendorlens.model_adapter import (
    ModelAuthenticationError,
    ModelProtocolError,
    ModelRateLimitError,
    ModelTransportError,
    OpenAICompatibleChatModel,
)


def model(handler, *, api_key="secret-value"):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleChatModel(
        base_url="https://models.example/v1",
        api_key=api_key,
        model="compatible-model",
        client=client,
    )


def test_chat_model_parses_content_and_usage():
    def handler(request):
        assert request.url == "https://models.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret-value"
        return httpx.Response(
            200,
            json={
                "model": "compatible-model",
                "choices": [{"message": {"role": "assistant", "content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        )

    response = model(handler).complete(messages=[{"role": "user", "content": "review"}])

    assert response.content == '{"ok":true}'
    assert response.usage.total_tokens == 14


def test_chat_model_parses_and_validates_tool_calls():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "compatible-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "calculate_tco",
                                        "arguments": '{"one_time_cost":800000}',
                                    },
                                }
                            ],
                        }
                    }
                ],
            },
        )

    response = model(handler).complete(messages=[], tools=[])

    assert response.tool_calls[0].name == "calculate_tco"
    assert response.tool_calls[0].arguments == {"one_time_cost": 800000}


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, ModelAuthenticationError), (429, ModelRateLimitError), (500, ModelTransportError)],
)
def test_chat_model_maps_http_errors_without_leaking_key(status, error_type):
    def handler(request):
        return httpx.Response(status, text="secret-value should not be repeated")

    adapter = model(handler)
    with pytest.raises(error_type) as caught:
        adapter.complete(messages=[])

    assert "secret-value" not in str(caught.value)
    assert "secret-value" not in repr(adapter)


def test_chat_model_rejects_invalid_tool_arguments():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "calculate_tco", "arguments": "nope"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    with pytest.raises(ModelProtocolError, match="tool arguments"):
        model(handler).complete(messages=[])


def test_chat_model_maps_timeout_to_transport_error():
    def handler(request):
        raise httpx.ConnectTimeout("secret-value", request=request)

    with pytest.raises(ModelTransportError, match="request failed") as caught:
        model(handler).complete(messages=[])

    assert "secret-value" not in str(caught.value)
