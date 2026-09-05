from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from ai_multi_agent_platform.adapters import OpenAICompatibleOnboardingAdapter
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
from ai_multi_agent_platform.observability import (
    InMemoryExporter,
    ObservedModelProvider,
    Telemetry,
)


class NativeStreamingProvider(ModelProvider):
    def __init__(self) -> None:
        self.generate_calls = 0
        self.stream_calls = 0
        self.discovery_calls = 0

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="native-stream-provider",
            provider_type="test-model",
            supported_operations=("generate", "stream", "list_native_models"),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.generate_calls += 1
        return ModelResponse(
            request_id=request.request_id,
            text="native stream",
            model_ref="provider-native-model",
            usage={"total_tokens": 9},
        )

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def iterate() -> AsyncIterator[ModelStreamEvent]:
            self.stream_calls += 1
            yield ModelStreamEvent(
                kind=ModelStreamEventKind.TEXT_DELTA,
                request_id=request.request_id,
                model_ref="provider-native-model",
                text_delta="native ",
            )
            yield ModelStreamEvent(
                kind=ModelStreamEventKind.TEXT_DELTA,
                request_id=request.request_id,
                model_ref="provider-native-model",
                text_delta="stream",
            )
            response = ModelResponse(
                request_id=request.request_id,
                text="native stream",
                model_ref="provider-native-model",
                usage={"total_tokens": 9},
            )
            yield ModelStreamEvent(
                kind=ModelStreamEventKind.COMPLETED,
                request_id=request.request_id,
                model_ref="provider-native-model",
                finish_reason="stop",
                usage={"total_tokens": 9},
                response=response,
            )

        return iterate()

    async def list_native_models(self) -> tuple[str, ...]:
        self.discovery_calls += 1
        return ("provider-native-model",)


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
            text="generated",
            model_ref="provider-private-model",
        )


class BlockingCancellationProvider(ModelProvider):
    def __init__(self) -> None:
        self.generate_started = asyncio.Event()
        self.stream_started = asyncio.Event()

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="blocking-provider",
            provider_type="test-model",
            supported_operations=("generate", "stream"),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.generate_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled generation unexpectedly resumed")

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def iterate() -> AsyncIterator[ModelStreamEvent]:
            self.stream_started.set()
            yield ModelStreamEvent(
                kind=ModelStreamEventKind.TEXT_DELTA,
                request_id=request.request_id,
                model_ref="provider-private-model",
                text_delta="started",
            )
            await asyncio.Event().wait()
            raise AssertionError("cancelled stream unexpectedly resumed")

        return iterate()


def model_config(provider_id: str, *, streaming: bool = True) -> ModelConfiguration:
    return ModelConfiguration(
        config_id="model-issue-10-completion",
        display_name="Issue 10 completion model",
        provider_id=provider_id,
        location=ModelLocation.LOCAL,
        health=HealthStatus.HEALTHY,
        capabilities=ModelCapabilities(streaming=streaming, modalities=("text",)),
    )


def runtime_for(provider: ModelProvider, *, streaming: bool = True) -> ModelRuntime:
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(model_config(provider.descriptor.provider_id, streaming=streaming))
    return ModelRuntime(registry)


def request(request_id: str, *, streaming: bool = False) -> ModelRequest:
    requirements: dict[str, JsonValue] = {
        "model_config_id": "model-issue-10-completion",
    }
    if streaming:
        requirements["streaming"] = True
    return ModelRequest(
        request_id=request_id,
        messages=("hello",),
        context=OperationContext(correlation_id=f"corr-{request_id}"),
        requirements=requirements,
    )


async def collect(stream: AsyncIterator[ModelStreamEvent]) -> tuple[ModelStreamEvent, ...]:
    return tuple([event async for event in stream])


def test_observed_provider_preserves_native_streaming_and_usage_observability() -> None:
    inner = NativeStreamingProvider()
    exporter = InMemoryExporter()
    observed = ObservedModelProvider(inner, Telemetry(exporter))
    runtime = runtime_for(observed)

    events = asyncio.run(collect(runtime.stream(request("observed-stream", streaming=True))))

    assert [event.kind for event in events] == [
        ModelStreamEventKind.TEXT_DELTA,
        ModelStreamEventKind.TEXT_DELTA,
        ModelStreamEventKind.COMPLETED,
    ]
    assert [event.text_delta for event in events[:-1]] == ["native ", "stream"]
    assert all(event.model_ref == "model-issue-10-completion" for event in events)
    assert inner.stream_calls == 1
    assert inner.generate_calls == 0

    stream_spans = [span for span in exporter.spans if span.name == "model.stream"]
    assert len(stream_spans) == 1
    assert stream_spans[0].outcome.value == "succeeded"
    usage = [metric for metric in exporter.metrics if metric.name == "platform.model.usage"]
    assert any(metric.attributes["usage_key"] == "total_tokens" for metric in usage)


def test_native_model_discovery_is_provider_neutral_and_survives_observability_wrapper() -> None:
    inner = NativeStreamingProvider()
    observed: ModelProvider = ObservedModelProvider(inner, Telemetry(InMemoryExporter()))
    adapter = OpenAICompatibleOnboardingAdapter()

    assert asyncio.run(observed.list_native_models()) == ("provider-native-model",)
    assert asyncio.run(adapter.list_native_models(observed)) == ("provider-native-model",)
    assert inner.discovery_calls == 2


def test_provider_without_native_discovery_remains_source_compatible() -> None:
    provider: ModelProvider = GenerateOnlyProvider()

    assert asyncio.run(provider.list_native_models()) == ()


def test_runtime_maps_generate_task_cancellation_to_canonical_error() -> None:
    async def scenario() -> ContractError:
        provider = BlockingCancellationProvider()
        runtime = runtime_for(provider, streaming=False)
        task = asyncio.create_task(runtime.generate(request("cancel-generate")))
        await provider.generate_started.wait()
        task.cancel()
        with pytest.raises(ContractError) as captured:
            await task
        return captured.value

    error = asyncio.run(scenario())

    assert error.code is ErrorCode.CANCELLED
    assert error.provider_id == "blocking-provider"
    assert error.retryable is False
    assert error.details["request_id"] == "cancel-generate"
    assert error.details["model_config_id"] == "model-issue-10-completion"


def test_runtime_maps_stream_consumer_cancellation_to_canonical_error() -> None:
    async def scenario() -> ContractError:
        provider = BlockingCancellationProvider()
        runtime = runtime_for(provider)

        async def consume() -> tuple[ModelStreamEvent, ...]:
            return await collect(runtime.stream(request("cancel-stream", streaming=True)))

        task = asyncio.create_task(consume())
        await provider.stream_started.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(ContractError) as captured:
            await task
        return captured.value

    error = asyncio.run(scenario())

    assert error.code is ErrorCode.CANCELLED
    assert error.provider_id == "blocking-provider"
    assert error.retryable is False
    assert error.details["request_id"] == "cancel-stream"
    assert error.details["model_config_id"] == "model-issue-10-completion"
