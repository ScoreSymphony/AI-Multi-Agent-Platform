from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

import ai_multi_agent_platform.adapters.litellm as litellm_adapter
from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.litellm import (
    LiteLLMMode,
    LiteLLMModelProvider,
    LiteLLMProviderConfig,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    JsonValue,
    ModelRequest,
    OperationContext,
)
from ai_multi_agent_platform.models import (
    CanonicalModelRequest,
    CanonicalModelResponse,
    ModelFinishReason,
    ModelGenerationParameters,
    ModelMessage,
    ModelRole,
    ModelToolDefinition,
    StructuredResponseExpectation,
    StructuredResponseKind,
)

CTX = OperationContext(correlation_id="corr-litellm")
LiteLLMCompletion = Callable[..., Awaitable[object]]


def make_library_provider(
    *,
    completion: LiteLLMCompletion,
    enabled: bool = True,
    timeout_seconds: float = 120.0,
) -> LiteLLMModelProvider:
    return LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-library",
            mode=LiteLLMMode.LIBRARY,
            models={"model-local-coder": "ollama/qwen3-coder"},
            enabled=enabled,
            base_url="http://127.0.0.1:11434",
            timeout_seconds=timeout_seconds,
        ),
        completion=completion,
    )


def test_library_adapter_translates_canonical_request_and_response() -> None:
    captured: dict[str, object] = {}

    async def completion(**kwargs: object) -> object:
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"answer":"local"}',
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "lookup",
                                    "arguments": '{"query":"abc"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }

    provider = make_library_provider(completion=completion)
    request = CanonicalModelRequest(
        request_id="req-litellm-library",
        context=CTX,
        system_instruction="Use the supplied tool when useful.",
        messages=(ModelMessage.text(ModelRole.USER, "Return JSON"),),
        tools=(
            ModelToolDefinition(
                tool_ref="tool.lookup",
                name="lookup",
                description="Look up a value",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
        ),
        response=StructuredResponseExpectation(
            kind=StructuredResponseKind.JSON_OBJECT,
        ),
        generation=ModelGenerationParameters(temperature=0.2, max_tokens=128),
        model_config_id="model-local-coder",
        task_id="task-litellm",
        run_id="run-litellm",
        agent_id="agent-litellm",
        routing_requirements={"local_only": True},
    ).to_contract_request()

    response = asyncio.run(provider.generate(request))
    canonical = CanonicalModelResponse.from_contract_response(response)

    assert captured["model"] == "ollama/qwen3-coder"
    assert captured["api_base"] == "http://127.0.0.1:11434"
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 128
    assert captured["response_format"] == {"type": "json_object"}
    assert "fallbacks" not in captured

    messages = captured["messages"]
    assert isinstance(messages, list)
    assert messages[0] == {
        "role": "system",
        "content": "Use the supplied tool when useful.",
    }
    assert messages[1] == {"role": "user", "content": "Return JSON"}

    assert response.model_ref == "model-local-coder"
    assert response.usage["total_tokens"] == 7
    assert canonical.finish_reason is ModelFinishReason.TOOL_CALL
    assert canonical.structured_output == {"answer": "local"}
    assert canonical.tool_calls[0].call_id == "call-1"
    assert canonical.tool_calls[0].tool_name == "lookup"
    assert canonical.tool_calls[0].arguments == {"query": "abc"}

    litellm_metadata = next(
        item for item in response.adapter_metadata if item.namespace == "litellm"
    )
    assert litellm_metadata.values["correlation_id"] == CTX.correlation_id
    assert litellm_metadata.values["task_id"] == "task-litellm"
    assert litellm_metadata.values["run_id"] == "run-litellm"
    assert litellm_metadata.values["agent_id"] == "agent-litellm"


def test_library_adapter_maps_litellm_errors_to_canonical_categories() -> None:
    class RateLimitError(Exception):
        pass

    async def completion(**kwargs: object) -> object:
        del kwargs
        raise RateLimitError("busy")

    provider = make_library_provider(completion=completion)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-litellm-rate-limit",
                    messages=("hello",),
                    context=CTX,
                    requirements={"model_config_id": "model-local-coder"},
                )
            )
        )

    assert captured.value.code is ErrorCode.RATE_LIMITED
    assert captured.value.retryable is True
    assert captured.value.provider_id == "litellm-library"
    assert captured.value.details == {"exception_type": "RateLimitError"}


def test_library_adapter_maps_timeout_to_canonical_error() -> None:
    async def completion(**kwargs: object) -> object:
        del kwargs
        await asyncio.sleep(0.05)
        return {}

    provider = make_library_provider(completion=completion, timeout_seconds=0.001)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-litellm-timeout",
                    messages=("hello",),
                    context=CTX,
                    requirements={"model_config_id": "model-local-coder"},
                )
            )
        )

    assert captured.value.code is ErrorCode.TIMEOUT
    assert captured.value.retryable is True


def test_library_adapter_maps_cancellation_to_canonical_error() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def completion(**kwargs: object) -> object:
            del kwargs
            started.set()
            await asyncio.sleep(60)
            return {}

        provider = make_library_provider(completion=completion)
        request = ModelRequest(
            request_id="req-litellm-cancelled",
            messages=("hello",),
            context=CTX,
            requirements={"model_config_id": "model-local-coder"},
        )
        task = asyncio.create_task(provider.generate(request))
        await started.wait()
        task.cancel()

        with pytest.raises(ContractError) as captured:
            await task

        assert captured.value.code is ErrorCode.CANCELLED

    asyncio.run(scenario())


def test_library_mode_fails_clearly_when_optional_dependency_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-library",
            mode=LiteLLMMode.LIBRARY,
            models={"model-local-coder": "ollama/qwen3-coder"},
        )
    )

    def missing_dependency(name: str) -> object:
        assert name == "litellm"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(litellm_adapter.importlib, "import_module", missing_dependency)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-litellm-missing",
                    messages=("hello",),
                    context=CTX,
                    requirements={"model_config_id": "model-local-coder"},
                )
            )
        )

    assert captured.value.code is ErrorCode.INVALID_CONFIGURATION
    assert captured.value.details["install_extra"] == "ai-multi-agent-platform[litellm]"


def test_disabled_adapter_is_unavailable_without_loading_litellm() -> None:
    async def completion(**kwargs: object) -> object:
        del kwargs
        raise AssertionError("disabled adapter must not invoke completion")

    provider = make_library_provider(completion=completion, enabled=False)

    assert asyncio.run(provider.health()) is HealthStatus.UNAVAILABLE
    assert provider.descriptor.available is False
    with pytest.raises(ContractError) as captured:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-litellm-disabled",
                    messages=("hello",),
                    context=CTX,
                )
            )
        )
    assert captured.value.code is ErrorCode.INVALID_CONFIGURATION


class RecordingProxyTransport:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                str,
                str,
                Mapping[str, str],
                Mapping[str, JsonValue] | None,
                float,
            ]
        ] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        self.calls.append((method, url, headers, payload, timeout_seconds))
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": "local-alias"}]})
        return HttpJsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "proxy-local-answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 4},
            },
        )


def test_proxy_mode_uses_existing_openai_compatible_path_for_local_gateway() -> None:
    transport = RecordingProxyTransport()
    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-proxy-local",
            mode=LiteLLMMode.PROXY,
            base_url="http://127.0.0.1:4000/v1",
            models={"model-local-coder": "local-alias"},
        ),
        proxy_transport=transport,
    )

    health = asyncio.run(provider.health())
    response = asyncio.run(
        provider.generate(
            ModelRequest(
                request_id="req-litellm-proxy",
                messages=("hello",),
                context=CTX,
                requirements={
                    "model_config_id": "model-local-coder",
                    "local_only": True,
                    "task_id": "task-proxy",
                },
            )
        )
    )

    assert health is HealthStatus.HEALTHY
    assert response.text == "proxy-local-answer"
    assert response.model_ref == "model-local-coder"
    assert transport.calls[0][1] == "http://127.0.0.1:4000/v1/models"
    assert transport.calls[1][1] == "http://127.0.0.1:4000/v1/chat/completions"
    payload = transport.calls[1][3]
    assert payload is not None
    assert payload["model"] == "local-alias"
    assert any(
        item.namespace == "litellm" and item.values["mode"] == "proxy"
        for item in response.adapter_metadata
    )


class LocalOpenAICompatibleHandler(BaseHTTPRequestHandler):
    paths: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.paths.append(self.path)
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._send_json({"data": [{"id": "local-alias"}]})

    def do_POST(self) -> None:
        self.paths.append(self.path)
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        self._send_json(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "real-local-answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 3},
            }
        )

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _send_json(self, payload: object) -> None:
        import json

        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def local_openai_compatible_endpoint() -> Iterator[str]:
    LocalOpenAICompatibleHandler.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalOpenAICompatibleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_proxy_mode_works_against_real_local_openai_compatible_http_endpoint() -> None:
    with local_openai_compatible_endpoint() as base_url:
        provider = LiteLLMModelProvider(
            LiteLLMProviderConfig(
                provider_id="litellm-proxy-real-local",
                mode=LiteLLMMode.PROXY,
                base_url=base_url,
                models={"model-local-coder": "local-alias"},
            )
        )

        health = asyncio.run(provider.health())
        response = asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-litellm-real-local",
                    messages=("hello",),
                    context=CTX,
                    requirements={"model_config_id": "model-local-coder", "local_only": True},
                )
            )
        )

    assert health is HealthStatus.HEALTHY
    assert response.text == "real-local-answer"
    assert response.model_ref == "model-local-coder"
    assert LocalOpenAICompatibleHandler.paths == [
        "/v1/models",
        "/v1/chat/completions",
    ]


def test_descriptor_never_exposes_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_TEST_KEY", "super-secret")
    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-library",
            mode=LiteLLMMode.LIBRARY,
            models={"model-a": "openai/model-a"},
            api_key_env="LITELLM_TEST_KEY",
        ),
        completion=lambda **kwargs: asyncio.sleep(0, result={}),
    )

    values = provider.descriptor.adapter_metadata[0].values
    assert values["credential_source"] == "env:LITELLM_TEST_KEY"
    assert "super-secret" not in repr(provider.descriptor)
