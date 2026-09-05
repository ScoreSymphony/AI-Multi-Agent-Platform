from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

import pytest

from ai_multi_agent_platform.adapters import (
    HttpJsonResponse,
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    JsonValue,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelStreamEventKind,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)

CTX = OperationContext(correlation_id="corr-model-stream")


class RecordingStreamingTransport:
    def __init__(
        self,
        *,
        text_parts: tuple[str, ...] = ("local ", "stream"),
        native_model: str = "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    ) -> None:
        self.text_parts = text_parts
        self.native_model = native_model
        self.json_calls: list[
            tuple[
                str,
                str,
                Mapping[str, str],
                Mapping[str, JsonValue] | None,
                float,
            ]
        ] = []
        self.stream_calls: list[
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
        self.json_calls.append((method, url, headers, payload, timeout_seconds))
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": self.native_model}]})
        return HttpJsonResponse(
            200,
            {
                "model": self.native_model,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "".join(self.text_parts),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 9},
            },
        )

    def stream_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> AsyncIterator[HttpJsonResponse]:
        self.stream_calls.append((method, url, headers, payload, timeout_seconds))

        async def iterate() -> AsyncIterator[HttpJsonResponse]:
            for part in self.text_parts:
                yield HttpJsonResponse(
                    200,
                    {
                        "choices": [
                            {
                                "delta": {"content": part},
                                "finish_reason": None,
                            }
                        ]
                    },
                )
            yield HttpJsonResponse(
                200,
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
                },
            )

        return iterate()


def make_provider(
    transport: RecordingStreamingTransport,
    *,
    native_model: str | None = None,
) -> OpenAICompatibleModelProvider:
    return OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id="local-stream-provider",
            base_url="http://127.0.0.1:8000/v1",
            models={"model-local-stream": native_model or transport.native_model},
        ),
        transport=transport,
    )


def make_config(*, streaming: bool = True) -> ModelConfiguration:
    return ModelConfiguration(
        config_id="model-local-stream",
        display_name="Local streaming model",
        provider_id="local-stream-provider",
        location=ModelLocation.LOCAL,
        health=HealthStatus.HEALTHY,
        priority=50,
        capabilities=ModelCapabilities(
            context_window=131_072,
            streaming=streaming,
            modalities=("text",),
        ),
    )


async def collect_events(stream: AsyncIterator[ModelStreamEvent]) -> tuple[ModelStreamEvent, ...]:
    return tuple([event async for event in stream])


def runtime_with_provider(
    provider: ModelProvider,
    *,
    config: ModelConfiguration | None = None,
) -> tuple[ModelRegistry, ModelRuntime]:
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(config or make_config())
    asyncio.run(registry.refresh_health())
    return registry, ModelRuntime(registry)


def stream_request(request_id: str) -> ModelRequest:
    return ModelRequest(
        request_id=request_id,
        messages=("Stream the answer",),
        context=CTX,
        requirements={"streaming": True, "local_only": True},
    )


def test_runtime_forwards_native_openai_compatible_chunks_canonically() -> None:
    transport = RecordingStreamingTransport()
    provider = make_provider(transport)
    _, runtime = runtime_with_provider(provider)

    events = asyncio.run(collect_events(runtime.stream(stream_request("req-native-stream"))))

    assert [event.kind for event in events] == [
        ModelStreamEventKind.TEXT_DELTA,
        ModelStreamEventKind.TEXT_DELTA,
        ModelStreamEventKind.COMPLETED,
    ]
    assert [event.text_delta for event in events[:-1]] == ["local ", "stream"]
    assert all(event.model_ref == "model-local-stream" for event in events)

    completed = events[-1]
    assert completed.finish_reason == "stop"
    assert completed.usage["total_tokens"] == 9
    assert completed.response is not None
    assert completed.response.text == "local stream"
    assert completed.response.model_ref == "model-local-stream"
    assert completed.response.usage["total_tokens"] == 9

    runtime_metadata = events[0].adapter_metadata[-1]
    assert runtime_metadata.namespace == "platform-model-runtime"
    assert runtime_metadata.values["correlation_id"] == CTX.correlation_id
    assert runtime_metadata.values["model_config_id"] == "model-local-stream"

    assert len(transport.stream_calls) == 1
    _, url, headers, payload, _ = transport.stream_calls[0]
    assert url == "http://127.0.0.1:8000/v1/chat/completions"
    assert headers["Accept"] == "text/event-stream"
    assert "Authorization" not in headers
    assert payload is not None
    assert payload["stream"] is True
    assert payload["model"] == "Qwen/Qwen3-Coder-30B-A3B-Instruct"


class GenerateOnlyProvider(ModelProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="generate-only-provider",
            provider_type="test-model",
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            request_id=request.request_id,
            text="fallback response",
            model_ref="provider-private-model",
            usage={"total_tokens": 3},
        )


def test_runtime_stream_falls_back_for_generate_only_provider() -> None:
    provider = GenerateOnlyProvider()
    config = ModelConfiguration(
        config_id="model-fallback",
        display_name="Fallback model",
        provider_id="generate-only-provider",
        location=ModelLocation.LOCAL,
        health=HealthStatus.HEALTHY,
        capabilities=ModelCapabilities(streaming=False, modalities=("text",)),
    )
    registry, runtime = runtime_with_provider(provider, config=config)

    request = ModelRequest(
        request_id="req-fallback-stream",
        messages=("hello",),
        context=CTX,
        requirements={"model_config_id": "model-fallback"},
    )
    events = asyncio.run(collect_events(runtime.stream(request)))

    assert [event.kind for event in events] == [
        ModelStreamEventKind.TEXT_DELTA,
        ModelStreamEventKind.COMPLETED,
    ]
    assert events[0].text_delta == "fallback response"
    assert all(event.model_ref == "model-fallback" for event in events)
    assert events[-1].response is not None
    assert events[-1].response.model_ref == "model-fallback"
    assert registry.get_model("model-fallback").config_id == "model-fallback"


def test_provider_replacement_preserves_canonical_stream_identity() -> None:
    first_transport = RecordingStreamingTransport(
        text_parts=("first",),
        native_model="native-model-v1",
    )
    first_provider = make_provider(first_transport, native_model="native-model-v1")
    registry, runtime = runtime_with_provider(first_provider)

    first_events = asyncio.run(collect_events(runtime.stream(stream_request("req-stream-v1"))))
    first_completed = first_events[-1]
    assert first_completed.response is not None
    assert first_completed.response.text == "first"
    assert first_completed.model_ref == "model-local-stream"

    second_transport = RecordingStreamingTransport(
        text_parts=("second",),
        native_model="native-model-v2",
    )
    second_provider = make_provider(second_transport, native_model="native-model-v2")
    registry.replace_provider(second_provider)
    asyncio.run(registry.refresh_health())

    second_events = asyncio.run(collect_events(runtime.stream(stream_request("req-stream-v2"))))
    second_completed = second_events[-1]
    assert second_completed.response is not None
    assert second_completed.response.text == "second"
    assert second_completed.model_ref == "model-local-stream"
    assert registry.get_model("model-local-stream").config_id == "model-local-stream"

    second_payload = second_transport.stream_calls[0][3]
    assert second_payload is not None
    assert second_payload["model"] == "native-model-v2"


def test_native_stream_timeout_is_mapped_to_canonical_error() -> None:
    class TimeoutStreamingTransport(RecordingStreamingTransport):
        def stream_json(
            self,
            method: str,
            url: str,
            *,
            headers: Mapping[str, str],
            payload: Mapping[str, JsonValue] | None,
            timeout_seconds: float,
        ) -> AsyncIterator[HttpJsonResponse]:
            async def iterate() -> AsyncIterator[HttpJsonResponse]:
                raise TimeoutError("socket timed out")
                yield HttpJsonResponse(200, None)

            return iterate()

    provider = make_provider(TimeoutStreamingTransport())
    _, runtime = runtime_with_provider(provider)

    with pytest.raises(ContractError) as captured:
        asyncio.run(collect_events(runtime.stream(stream_request("req-stream-timeout"))))

    assert captured.value.code is ErrorCode.TIMEOUT
    assert captured.value.retryable is True
    assert captured.value.provider_id == "local-stream-provider"
