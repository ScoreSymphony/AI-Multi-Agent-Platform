"""Deterministic model selection over the platform-owned registry."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import ModelRouter
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    HealthStatus,
    ModelRequest,
    ModelSelection,
    ProviderDescriptor,
)

from .registry import ModelRegistry
from .types import ModelConfiguration, ModelLocation, ModelRoute, RoutingRequirements


class DeterministicModelRouter(ModelRouter):
    """First-pass explainable routing policy required by issue #10."""

    descriptor = ProviderDescriptor(
        provider_id="platform-model-router",
        provider_type="model-router",
        supported_operations=("select_provider",),
        capabilities=(
            Capability(
                name="model.routing.deterministic",
                kind=CapabilityKind.MODEL,
                supported_operations=("select_provider",),
                features=("capability-filtering", "local-policy", "explicit-assignment"),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )

    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    async def select_provider(self, request: ModelRequest) -> ModelSelection:
        try:
            requirements = RoutingRequirements.from_request(request)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"invalid model routing requirements: {exc}",
            ) from exc

        route = self.route(requirements)
        return ModelSelection(
            provider_id=route.provider_id,
            model_ref=route.model_config_id,
            adapter_metadata=(
                AdapterMetadata(
                    namespace="platform-model-router",
                    values={
                        "reason": route.reason,
                        "candidate_ids": list(route.candidate_ids),
                        "correlation_id": request.context.correlation_id,
                    },
                ),
            ),
        )

    def route(self, requirements: RoutingRequirements) -> ModelRoute:
        if requirements.explicit_model_id is not None:
            try:
                explicit = self.registry.get_model(requirements.explicit_model_id)
            except ContractError as exc:
                raise ContractError(
                    ErrorCode.NO_COMPATIBLE_ROUTE,
                    f"explicit model assignment is unavailable: {requirements.explicit_model_id}",
                    details={"explicit_model_id": requirements.explicit_model_id},
                ) from exc
            if not self._eligible(explicit, requirements):
                raise ContractError(
                    ErrorCode.NO_COMPATIBLE_ROUTE,
                    f"explicit model assignment does not satisfy routing policy: {explicit.config_id}",
                    provider_id=explicit.provider_id,
                    details={"explicit_model_id": explicit.config_id},
                )
            return ModelRoute(
                model_config_id=explicit.config_id,
                provider_id=explicit.provider_id,
                reason="explicit canonical model assignment",
                candidate_ids=(explicit.config_id,),
            )

        candidates = [
            config
            for config in self.registry.list_models(enabled=True)
            if self._eligible(config, requirements)
        ]
        candidates.sort(key=lambda item: (-item.priority, item.config_id))

        if not candidates:
            raise ContractError(
                ErrorCode.NO_COMPATIBLE_ROUTE,
                "no registered model satisfies the requested capabilities and policy",
                details={
                    "local_only": requirements.local_only,
                    "self_hosted_only": requirements.self_hosted_only,
                    "tool_calling": requirements.tool_calling,
                    "structured_output": requirements.structured_output,
                    "streaming": requirements.streaming,
                    "modalities": list(requirements.modalities),
                    "min_context_window": requirements.min_context_window,
                },
            )

        selected = candidates[0]
        return ModelRoute(
            model_config_id=selected.config_id,
            provider_id=selected.provider_id,
            reason="highest deterministic priority among compatible registered models",
            candidate_ids=tuple(item.config_id for item in candidates),
        )

    def _eligible(
        self,
        config: ModelConfiguration,
        requirements: RoutingRequirements,
    ) -> bool:
        if not config.enabled:
            return False

        try:
            provider = self.registry.get_provider(config.provider_id)
        except ContractError:
            return False
        if not provider.descriptor.available:
            return False

        if self.registry.effective_health(config) not in {
            HealthStatus.HEALTHY,
            HealthStatus.DEGRADED,
        }:
            return False

        if requirements.local_only and config.location is not ModelLocation.LOCAL:
            return False
        if requirements.self_hosted_only and config.location not in {
            ModelLocation.LOCAL,
            ModelLocation.SELF_HOSTED,
        }:
            return False

        capabilities = config.capabilities
        if requirements.min_context_window is not None:
            if capabilities.context_window is None:
                return False
            if capabilities.context_window < requirements.min_context_window:
                return False
        if requirements.tool_calling and not capabilities.tool_calling:
            return False
        if requirements.structured_output and not capabilities.structured_output:
            return False
        if requirements.streaming and not capabilities.streaming:
            return False
        if requirements.modalities and not set(requirements.modalities).issubset(
            capabilities.modalities
        ):
            return False
        return True
