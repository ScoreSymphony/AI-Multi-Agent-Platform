from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ai_multi_agent_platform.adapters import (
    HttpSseEvent,
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    HealthStatus,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelResponseChunk,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelRegistry,
    ModelRuntime,
)


class _StreamingProvider(ModelProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="streaming-provider",
            provider_type="test",
            supported_operations=("generate", "stream"),
            capabilities=(
                Capability(
                    name="model.stream",
                    kind=CapabilityKind.MODEL,
                    supported_operations=("generate", "stream"),
                ),
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            request_id=request.request_id,
            text="complete response",
            model_ref="private-model",
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelResponseChunk]:
        yield ModelResponseChunk(
            request_id=request.request_id,
            text="first ",
            model_ref="private-model",
        )
        yield ModelResponseChunk(
            request_id=request.request_id,
            text="second",
            model_ref="private-model",
            is_final=True,
        )


async def _collect(stream: AsyncIterator[ModelResponseChunk]) -> tuple[ModelResponseChunk, ...]:
    return tuple([item async for item in stream])


class _OpenAIStreamingTransport:
    async def request_json(self, *args: str, **kwargs: str) -> None:
        raise AssertionError("streaming must not use the JSON response transport")

    def stream_sse(self, *args: str, **kwargs: str) -> AsyncIterator[HttpSseEvent]:
        async def events() -> AsyncIterator[HttpSseEvent]:
            yield HttpSseEvent(
                status_code=200,
                data='{"choices":[{"delta":{"content":"native "}}]}',
            )
            yield HttpSseEvent(
                status_code=200,
                data='{"choices":[{"delta":{"content":"chunks"},"finish_reason":"stop"}]}',
            )
            yield HttpSseEvent(status_code=200, data="[DONE]")

        return events()


def test_model_runtime_stream_preserves_canonical_routing_identity() -> None:
    # Given: a canonical model configuration backed by a provider-private model.
    registry = ModelRegistry()
    provider = _StreamingProvider()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model-streaming",
            display_name="Streaming model",
            provider_id=provider.descriptor.provider_id,
            health=HealthStatus.HEALTHY,
            capabilities=ModelCapabilities(context_window=4096, streaming=True),
        )
    )
    runtime = ModelRuntime(registry)

    # When: the canonical runtime streams a routed request.
    chunks = asyncio.run(
        _collect(
            runtime.stream(
                ModelRequest(
                    request_id="request-streaming",
                    messages=("hello",),
                    context=OperationContext(correlation_id="correlation-streaming"),
                    requirements={"model_config_id": "model-streaming", "streaming": True},
                )
            )
        )
    )

    # Then: chunk identity is canonical and private provider identity stays metadata-only.
    assert [chunk.text for chunk in chunks] == ["first ", "second"]
    assert all(chunk.model_ref == "model-streaming" for chunk in chunks)
    assert chunks[-1].is_final is True
    assert chunks[0].adapter_metadata[-1].namespace == "platform-model-runtime"


def test_openai_compatible_provider_translates_native_sse_chunks() -> None:
    # Given: a local OpenAI-compatible provider emitting native SSE chunks.
    provider = OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id="local-streaming-provider",
            base_url="http://127.0.0.1:8000/v1",
            models={"model-local-streaming": "native-streaming-model"},
        ),
        transport=_OpenAIStreamingTransport(),
    )

    # When: the provider streams one canonical request.
    chunks = asyncio.run(
        _collect(
            provider.stream(
                ModelRequest(
                    request_id="request-native-stream",
                    messages=("hello",),
                    context=OperationContext(correlation_id="correlation-native-stream"),
                    requirements={"model_config_id": "model-local-streaming"},
                )
            )
        )
    )

    # Then: native chunks become provider-neutral text chunks with a terminal signal.
    assert [chunk.text for chunk in chunks] == ["native ", "chunks"]
    assert chunks[-1].is_final is True
    assert all(chunk.model_ref == "model-local-streaming" for chunk in chunks)
