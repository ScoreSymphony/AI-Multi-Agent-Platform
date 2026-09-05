"""Deterministic model selection over the platform-owned registry."""

from __future__ import annotations

from dataclasses import replace

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
from .routing_profiles import ModelRoutingProfileRevision, RoutingProfileFallbackPolicy
from .types import ModelConfiguration, ModelLocation, ModelRoute, RoutingRequirements


class DeterministicModelRouter(ModelRouter):
    """Explainable routing policy over canonical model inventory and profile revisions."""

    descriptor = ProviderDescriptor(
        provider_id="platform-model-router",
        provider_type="model-router",
        supported_operations=("select_provider",),
        capabilities=(
            Capability(
                name="model.routing.deterministic",
                kind=CapabilityKind.MODEL,
                supported_operations=("select_provider",),
                features=(
                    "capability-filtering",
                    "local-policy",
                    "explicit-assignment",
                    "versioned-routing-profile",
                ),
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

    def route_profile(self, profile: ModelRoutingProfileRevision) -> ModelRoute:
        """Route against one exact immutable routing-profile revision.

        The profile contributes policy but never takes over registry or routing ownership.
        Preferred model references are canonical ``ModelConfiguration`` IDs. Provider-native
        names and current provider health remain entirely in the registry/provider layer.
        """

        requirements = profile.policy.requirements
        preferred_ids: list[str] = []
        if requirements.explicit_model_id is not None:
            preferred_ids.append(requirements.explicit_model_id)
        preferred_ids.extend(
            model_id
            for model_id in profile.policy.preferred_model_ids
            if model_id not in preferred_ids
        )

        failures: list[str] = []
        for model_id in preferred_ids:
            try:
                route = self.route(replace(requirements, explicit_model_id=model_id))
            except ContractError as exc:
                if exc.code is not ErrorCode.NO_COMPATIBLE_ROUTE:
                    raise
                failures.append(model_id)
                continue
            return replace(
                route,
                reason=(
                    f"routing profile {profile.ref.canonical_ref}: selected ordered "
                    f"canonical preference {model_id}"
                ),
            )

        if preferred_ids and profile.policy.fallback is RoutingProfileFallbackPolicy.FAIL:
            raise ContractError(
                ErrorCode.NO_COMPATIBLE_ROUTE,
                "no configured routing-profile model preference is currently compatible",
                details={
                    "routing_profile_ref": profile.ref.canonical_ref,
                    "preferred_model_ids": preferred_ids,
                    "failed_preference_ids": failures,
                    "fallback": profile.policy.fallback.value,
                },
            )

        fallback_requirements = replace(requirements, explicit_model_id=None)
        route = self.route(fallback_requirements)
        return replace(
            route,
            reason=(
                f"routing profile {profile.ref.canonical_ref}: deterministic registry fallback; "
                f"{route.reason}"
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
                message = (
                    "explicit model assignment does not satisfy routing policy: "
                    f"{explicit.config_id}"
                )
                raise ContractError(
                    ErrorCode.NO_COMPATIBLE_ROUTE,
                    message,
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
            for config in self.registry.query_models(
                enabled=True,
                min_context_window=requirements.min_context_window,
                tool_calling=True if requirements.tool_calling else None,
                structured_output=True if requirements.structured_output else None,
                streaming=True if requirements.streaming else None,
                modalities=requirements.modalities,
                reasoning=requirements.reasoning,
            )
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
                    "reasoning": list(requirements.reasoning),
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
        if requirements.reasoning and not set(requirements.reasoning).issubset(
            capabilities.reasoning
        ):
            return False
        return True
