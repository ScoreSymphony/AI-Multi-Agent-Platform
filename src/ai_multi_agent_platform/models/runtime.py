"""Platform-owned model routing and invocation service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelSelection,
    ModelStreamEvent,
    ModelStreamEventKind,
    StreamingModelProvider,
)

from .protocol import CanonicalModelRequest, CanonicalModelResponse
from .registry import ModelRegistry
from .router import DeterministicModelRouter
from .types import ModelConfiguration


class ModelRuntime:
    """Route and invoke a model without exposing provider-native identities."""

    def __init__(
        self,
        registry: ModelRegistry,
        router: DeterministicModelRouter | None = None,
    ) -> None:
        self.registry = registry
        self.router = router or DeterministicModelRouter(registry)

    async def select(self, request: ModelRequest) -> ModelSelection:
        return await self.router.select_provider(request)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        config, provider, routed_request = await self._resolve_target(request)
        provider_response = await provider.generate(routed_request)
        return self._normalize_response(request, config, provider_response)

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Route once and expose canonical incremental events.

        Providers that implement ``StreamingModelProvider`` are consumed natively.
        Other ``ModelProvider`` implementations retain full compatibility through a
        deterministic one-chunk fallback built from ``generate``.
        """

        async def iterate() -> AsyncIterator[ModelStreamEvent]:
            config, provider, routed_request = await self._resolve_target(request)
            if isinstance(provider, StreamingModelProvider):
                provider_stream = provider.stream(routed_request)
            else:
                provider_stream = self._fallback_stream(provider, routed_request)

            async for event in provider_stream:
                if event.request_id != request.request_id:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "model provider stream event request_id does not match request",
                        provider_id=config.provider_id,
                        details={
                            "expected_request_id": request.request_id,
                            "reported_request_id": event.request_id,
                        },
                    )

                provider_reported_model_ref = event.model_ref
                response = event.response
                if response is not None:
                    response = self._normalize_response(request, config, response)

                runtime_metadata = self._runtime_metadata(
                    request,
                    config,
                    provider_reported_model_ref,
                )
                yield replace(
                    event,
                    model_ref=config.config_id,
                    response=response,
                    adapter_metadata=event.adapter_metadata + (runtime_metadata,),
                )

        return iterate()

    async def generate_canonical(
        self,
        request: CanonicalModelRequest,
    ) -> CanonicalModelResponse:
        """Execute the rich issue-#10 request shape through the stable provider seam."""

        response = await self.generate(request.to_contract_request())
        return CanonicalModelResponse.from_contract_response(response)

    def stream_canonical(
        self,
        request: CanonicalModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream a rich request through the same canonical routing/runtime seam."""

        return self.stream(request.to_contract_request())

    async def _resolve_target(
        self,
        request: ModelRequest,
    ) -> tuple[ModelConfiguration, ModelProvider, ModelRequest]:
        selection = await self.select(request)
        if selection.model_ref is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "model router returned no canonical model configuration ID",
                provider_id=selection.provider_id,
            )

        config = self.registry.get_model(selection.model_ref)
        if config.provider_id != selection.provider_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "router provider selection does not match model registry configuration",
                provider_id=selection.provider_id,
                details={
                    "model_config_id": config.config_id,
                    "registry_provider_id": config.provider_id,
                },
            )

        provider = self.registry.get_provider(selection.provider_id)
        requirements = dict(request.requirements)
        requirements["model_config_id"] = config.config_id
        routed_request = replace(request, requirements=requirements)
        return config, provider, routed_request

    def _normalize_response(
        self,
        request: ModelRequest,
        config: ModelConfiguration,
        provider_response: ModelResponse,
    ) -> ModelResponse:
        if provider_response.request_id != request.request_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "model provider response request_id does not match request",
                provider_id=config.provider_id,
                details={
                    "expected_request_id": request.request_id,
                    "reported_request_id": provider_response.request_id,
                },
            )
        runtime_metadata = self._runtime_metadata(
            request,
            config,
            provider_response.model_ref,
        )
        return replace(
            provider_response,
            model_ref=config.config_id,
            adapter_metadata=provider_response.adapter_metadata + (runtime_metadata,),
        )

    def _runtime_metadata(
        self,
        request: ModelRequest,
        config: ModelConfiguration,
        provider_reported_model_ref: str,
    ) -> AdapterMetadata:
        return AdapterMetadata(
            namespace="platform-model-runtime",
            values={
                "model_config_id": config.config_id,
                "provider_id": config.provider_id,
                "provider_reported_model_ref": provider_reported_model_ref,
                "correlation_id": request.context.correlation_id,
            },
        )

    async def _fallback_stream(
        self,
        provider: ModelProvider,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        response = await provider.generate(request)
        if response.text:
            yield ModelStreamEvent(
                kind=ModelStreamEventKind.TEXT_DELTA,
                request_id=response.request_id,
                model_ref=response.model_ref,
                text_delta=response.text,
            )
        yield ModelStreamEvent(
            kind=ModelStreamEventKind.COMPLETED,
            request_id=response.request_id,
            model_ref=response.model_ref,
            usage=dict(response.usage),
            response=response,
        )
