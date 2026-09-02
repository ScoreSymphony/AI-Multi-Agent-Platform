"""Platform-owned model routing and invocation service."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ModelRequest,
    ModelResponse,
    ModelSelection,
)

from .registry import ModelRegistry
from .router import DeterministicModelRouter


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
        provider_response = await provider.generate(routed_request)

        runtime_metadata = AdapterMetadata(
            namespace="platform-model-runtime",
            values={
                "model_config_id": config.config_id,
                "provider_id": config.provider_id,
                "provider_reported_model_ref": provider_response.model_ref,
                "correlation_id": request.context.correlation_id,
            },
        )
        return replace(
            provider_response,
            model_ref=config.config_id,
            adapter_metadata=provider_response.adapter_metadata + (runtime_metadata,),
        )
