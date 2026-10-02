from __future__ import annotations

import json
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError


class ModelError(RuntimeError):
    pass


class ModelConfigurationError(ModelError):
    pass


class ModelTransportError(ModelError):
    pass


class ModelAuthenticationError(ModelError):
    pass


class ModelRateLimitError(ModelError):
    pass


class ModelProtocolError(ModelError):
    pass


class ModelToolCall(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class ModelUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ModelResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    model: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    usage_reported: bool = False


class ChatModel(Protocol):
    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ModelResponse: ...


class OpenAICompatibleChatModel:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        store: bool | None = False,
        request_options: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ):
        if not base_url.strip() or not api_key.strip() or not model.strip():
            raise ModelConfigurationError("model base URL, API key, and model name are required")
        if timeout_seconds <= 0:
            raise ModelConfigurationError("model timeout must be positive")
        request_options = dict(request_options or {})
        reserved = {"model", "messages", "tools", "tool_choice", "store"}
        if reserved & set(request_options):
            raise ModelConfigurationError("model request options contain reserved fields")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.store = store
        self.request_options = request_options
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, "
            f"model={self.model!r}, api_key='**********')"
        )

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ModelResponse:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.store is not None:
            payload["store"] = self.store
        payload.update(self.request_options)
        if tools:
            payload.update({"tools": tools, "tool_choice": tool_choice or "auto"})
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise ModelTransportError("model request failed") from error
        self._raise_for_status(response.status_code)
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            tool_calls = [self._parse_tool_call(item) for item in message.get("tool_calls") or []]
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise TypeError("content must be text")
            if content is None and not tool_calls:
                raise ValueError("response has neither content nor tool calls")
            return ModelResponse(
                content=content,
                tool_calls=tool_calls,
                model=body.get("model"),
                usage=ModelUsage.model_validate(body.get("usage") or {}),
                usage_reported=isinstance(body.get("usage"), dict)
                and all(
                    key in body["usage"]
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                ),
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as error:
            if isinstance(error, ModelProtocolError):
                raise
            raise ModelProtocolError(
                "model returned an invalid Chat Completions response"
            ) from error

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code in (401, 403):
            raise ModelAuthenticationError("model authentication failed")
        if status_code == 429:
            raise ModelRateLimitError("model rate limit exceeded")
        raise ModelTransportError(f"model service returned HTTP {status_code}")

    @staticmethod
    def _parse_tool_call(payload: dict[str, Any]) -> ModelToolCall:
        try:
            function = payload["function"]
            arguments = json.loads(function["arguments"])
            if not isinstance(arguments, dict):
                raise TypeError("tool arguments must be an object")
            return ModelToolCall(id=payload["id"], name=function["name"], arguments=arguments)
        except (KeyError, TypeError, json.JSONDecodeError, ValidationError) as error:
            raise ModelProtocolError("model returned invalid tool arguments") from error
