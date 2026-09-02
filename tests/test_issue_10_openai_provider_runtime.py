from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from ai_multi_agent_platform.adapters import (
    HttpJsonResponse,
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    HealthStatus,
    JsonValue,
    ModelRequest,
    OperationContext,
)
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)

CTX = OperationContext(correlation_id="corr-openai-compatible")


class RecordingTransport:
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
        self.fail_with: BaseException | None = None

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
        if self.fail_with is not None:
            raise self.fail_with
        if url.endswith("/models"):
            return HttpJsonResponse(
                200,
                {"data": [{"id": "Qwen/Qwen3-Coder-30B-A3B-Instruct"}]},
            )
        structured = payload is not None and payload.get("response_format") is not None
        content = '{"answer":"local answer"}' if structured else "local answer"
        return HttpJsonResponse(
            200,
            {
                "model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            },
        )


def make_provider(
    transport: RecordingTransport,
    *,
    models: Mapping[str, str] | None = None,
) -> OpenAICompatibleModelProvider:
    return OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id="local-model-endpoint",
            base_url="http://127.0.0.1:8000/v1",
            models=models or {"model-local-coder": "Qwen/Qwen3-Coder-30B-A3B-Instruct"},
        ),
        transport=transport,
    )


def make_config() -> ModelConfiguration:
    return ModelConfiguration(
        config_id="model-local-coder",
        display_name="Local coding model",
        provider_id="local-model-endpoint",
        aliases=("coding-default",),
        location=ModelLocation.LOCAL,
        health=HealthStatus.HEALTHY,
        priority=50,
        capabilities=ModelCapabilities(
            context_window=131_072,
            tool_calling=True,
            structured_output=True,
            streaming=False,
            modalities=("text",),
            reasoning=("reasoning",),
        ),
        adapter_metadata=(
            AdapterMetadata(
                namespace="openai-compatible",
                values={"model": "Qwen/Qwen3-Coder-30B-A3B-Instruct"},
            ),
        ),
    )


def test_local_provider_lists_models_without_paid_credentials() -> None:
    transport = RecordingTransport()
    provider = make_provider(transport)

    models = asyncio.run(provider.list_native_models())
    health = asyncio.run(provider.health())

    assert models == ("Qwen/Qwen3-Coder-30B-A3B-Instruct",)
    assert health is HealthStatus.HEALTHY
    assert all("Authorization" not in call[2] for call in transport.calls)
    assert transport.calls[0][1] == "http://127.0.0.1:8000/v1/models"


def test_runtime_routes_canonical_model_then_provider_resolves_native_name() -> None:
    transport = RecordingTransport()
    provider = make_provider(transport)
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(make_config())
    asyncio.run(registry.refresh_health())
    runtime = ModelRuntime(registry)

    response = asyncio.run(
        runtime.generate(
            ModelRequest(
                request_id="req-local-runtime",
                messages=("Return JSON",),
                context=CTX,
                requirements={
                    "local_only": True,
                    "structured_output": True,
                    "min_context_window": 32_000,
                    "temperature": 0.2,
                },
            )
        )
    )

    assert response.text == '{"answer":"local answer"}'
    assert response.model_ref == "model-local-coder"
    assert response.usage["total_tokens"] == 7
    assert response.adapter_metadata[-1].namespace == "platform-model-runtime"
    assert response.adapter_metadata[-1].values["correlation_id"] == CTX.correlation_id

    generation_call = transport.calls[-1]
    assert generation_call[1] == "http://127.0.0.1:8000/v1/chat/completions"
    payload = generation_call[3]
    assert payload is not None
    assert payload["model"] == "Qwen/Qwen3-Coder-30B-A3B-Instruct"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0.2


def test_provider_requires_canonical_target_when_multiple_models_are_configured() -> None:
    transport = RecordingTransport()
    provider = make_provider(
        transport,
        models={
            "model-a": "native-a",
            "model-b": "native-b",
        },
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-missing-target",
                    messages=("hello",),
                    context=CTX,
                )
            )
        )

    assert captured.value.code is ErrorCode.INVALID_REQUEST
    assert not transport.calls


def test_provider_maps_http_failures_to_canonical_errors() -> None:
    class RateLimitedTransport(RecordingTransport):
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
            return HttpJsonResponse(429, {"error": {"message": "busy"}})

    transport = RateLimitedTransport()
    provider = make_provider(transport)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-rate-limit",
                    messages=("hello",),
                    context=CTX,
                    requirements={"model_config_id": "model-local-coder"},
                )
            )
        )

    assert captured.value.code is ErrorCode.RATE_LIMITED
    assert captured.value.retryable is True
    assert captured.value.provider_id == "local-model-endpoint"


def test_provider_maps_timeout_without_leaking_transport_exception() -> None:
    transport = RecordingTransport()
    transport.fail_with = TimeoutError("socket timed out")
    provider = make_provider(transport)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-timeout",
                    messages=("hello",),
                    context=CTX,
                    requirements={"model_config_id": "model-local-coder"},
                )
            )
        )

    assert captured.value.code is ErrorCode.TIMEOUT
    assert captured.value.retryable is True


def test_provider_rejects_invalid_provider_response_canonically() -> None:
    class InvalidTransport(RecordingTransport):
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
            return HttpJsonResponse(200, {"unexpected": True})

    provider = make_provider(InvalidTransport())

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-invalid-response",
                    messages=("hello",),
                    context=CTX,
                    requirements={"model_config_id": "model-local-coder"},
                )
            )
        )

    assert captured.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


def test_missing_secret_reference_fails_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCAL_MODEL_TOKEN", raising=False)
    transport = RecordingTransport()
    provider = OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id="authenticated-local-model",
            base_url="http://127.0.0.1:9000/v1",
            models={"model-auth": "native-auth"},
            api_key_env="LOCAL_MODEL_TOKEN",
        ),
        transport=transport,
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(provider.list_native_models())

    assert captured.value.code is ErrorCode.INVALID_CONFIGURATION
    assert not transport.calls
