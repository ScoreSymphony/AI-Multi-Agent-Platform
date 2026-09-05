"""Platform-owned model routing and invocation service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ModelRequest,
    ModelResponse,
    ModelResponseChunk,
    ModelProvider,
    ModelSelection,
)

from .protocol import CanonicalModelRequest, CanonicalModelResponse
from .registry import ModelRegistry
from .router import DeterministicModelRouter
from .types import ModelConfiguration


@dataclass(frozen=True, slots=True)
class _RoutedModelRequest:
    config: ModelConfiguration
    provider: ModelProvider
    request: ModelRequest


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
        routed = await self._route(request)
        provider_response = await routed.provider.generate(routed.request)

        runtime_metadata = AdapterMetadata(
            namespace="platform-model-runtime",
            values={
                "model_config_id": routed.config.config_id,
                "provider_id": routed.config.provider_id,
                "provider_reported_model_ref": provider_response.model_ref,
                "correlation_id": request.context.correlation_id,
            },
        )
        return replace(
            provider_response,
            model_ref=routed.config.config_id,
            adapter_metadata=provider_response.adapter_metadata + (runtime_metadata,),
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelResponseChunk]:
        """Route one request and expose provider chunks with canonical model identity."""
        routed = await self._route(request)
        async for chunk in routed.provider.stream(routed.request):
            runtime_metadata = AdapterMetadata(
                namespace="platform-model-runtime",
                values={
                    "model_config_id": routed.config.config_id,
                    "provider_id": routed.config.provider_id,
                    "provider_reported_model_ref": chunk.model_ref,
                    "correlation_id": request.context.correlation_id,
                },
            )
            yield replace(
                chunk,
                model_ref=routed.config.config_id,
                adapter_metadata=chunk.adapter_metadata + (runtime_metadata,),
            )

    async def generate_canonical(
        self,
        request: CanonicalModelRequest,
    ) -> CanonicalModelResponse:
        """Execute the rich issue-#10 request shape through the stable provider seam."""

        response = await self.generate(request.to_contract_request())
        return CanonicalModelResponse.from_contract_response(response)

    async def _route(self, request: ModelRequest) -> _RoutedModelRequest:
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

        requirements = dict(request.requirements)
        requirements["model_config_id"] = config.config_id
        return _RoutedModelRequest(
            config=config,
            provider=self.registry.get_provider(selection.provider_id),
            request=replace(request, requirements=requirements),
        )
