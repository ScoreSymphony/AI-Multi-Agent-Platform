"""Deterministic model selection over the platform-owned registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import ModelRouter
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    ModelRequest,
    ModelSelection,
    ProviderDescriptor,
)

from .registry import ModelRegistry
from .routing_profiles import ModelRoutingProfileRevision, RoutingProfileFallbackPolicy
from .types import ModelConfiguration, ModelLocation, ModelRoute, RoutingRequirements

type CandidatePolicyHook = Callable[
    [ModelRequest, ModelConfiguration], Awaitable[tuple[bool, str | None]]
]


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
                    "candidate-policy-filtering",
                ),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        candidate_policy_hook: CandidatePolicyHook | None = None,
    ) -> None:
        self.registry = registry
        self._candidate_policy_hook = candidate_policy_hook

    async def select_provider(self, request: ModelRequest) -> ModelSelection:
        try:
            requirements = RoutingRequirements.from_request(request)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"invalid model routing requirements: {exc}",
            ) from exc

        exclusions: tuple[dict[str, str], ...] = ()
        if self._candidate_policy_hook is None:
            route = self.route(requirements)
        else:
            route, exclusions = await self._route_with_candidate_policy(request, requirements)
        metadata: dict[str, JsonValue] = {
            "reason": route.reason,
            "candidate_ids": list(route.candidate_ids),
            "correlation_id": request.context.correlation_id,
        }
        if exclusions:
            metadata["policy_exclusions"] = [dict(item) for item in exclusions]
        return ModelSelection(
            provider_id=route.provider_id,
            model_ref=route.model_config_id,
            adapter_metadata=(
                AdapterMetadata(
                    namespace="platform-model-router",
                    values=metadata,
                ),
            ),
        )

    async def _route_with_candidate_policy(
        self,
        request: ModelRequest,
        requirements: RoutingRequirements,
    ) -> tuple[ModelRoute, tuple[dict[str, str], ...]]:
        hook = self._candidate_policy_hook
        if hook is None:
            return self.route(requirements), ()

        if requirements.explicit_model_id is not None:
            route = self.route(requirements)
            config = self.registry.get_model(route.model_config_id)
            allowed, reason = await hook(request, config)
            if not allowed:
                exclusion = {
                    "model_config_id": config.config_id,
                    "provider_id": config.provider_id,
                    "reason": reason or "candidate_policy_denied",
                }
                raise ContractError(
                    ErrorCode.NO_COMPATIBLE_ROUTE,
                    "explicit model assignment is prohibited by candidate policy",
                    provider_id=config.provider_id,
                    details={
                        "explicit_model_id": config.config_id,
                        "policy_exclusions": cast(JsonValue, [exclusion]),
                    },
                )
            return route, ()

        candidates = self._candidate_configs(requirements)
        exclusions: list[dict[str, str]] = []
        for candidate in candidates:
            allowed, reason = await hook(request, candidate)
            if allowed:
                return (
                    ModelRoute(
                        model_config_id=candidate.config_id,
                        provider_id=candidate.provider_id,
                        reason=(
                            "highest deterministic priority among capability- and "
                            "policy-compatible registered models"
                        ),
                        candidate_ids=tuple(item.config_id for item in candidates),
                    ),
                    tuple(exclusions),
                )
            exclusions.append(
                {
                    "model_config_id": candidate.config_id,
                    "provider_id": candidate.provider_id,
                    "reason": reason or "candidate_policy_denied",
                }
            )

        raise ContractError(
            ErrorCode.NO_COMPATIBLE_ROUTE,
            "no registered model satisfies both routing and candidate policy",
            details={
                **self._no_route_details(requirements),
                "policy_exclusions": cast(JsonValue, exclusions),
            },
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
                    "preferred_model_ids": cast(JsonValue, preferred_ids),
                    "failed_preference_ids": cast(JsonValue, failures),
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

        candidates = self._candidate_configs(requirements)
        if not candidates:
            raise ContractError(
                ErrorCode.NO_COMPATIBLE_ROUTE,
                "no registered model satisfies the requested capabilities and policy",
                details=self._no_route_details(requirements),
            )

        selected = candidates[0]
        return ModelRoute(
            model_config_id=selected.config_id,
            provider_id=selected.provider_id,
            reason="highest deterministic priority among compatible registered models",
            candidate_ids=tuple(item.config_id for item in candidates),
        )

    def _candidate_configs(self, requirements: RoutingRequirements) -> list[ModelConfiguration]:
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
        return candidates

    @staticmethod
    def _no_route_details(requirements: RoutingRequirements) -> dict[str, JsonValue]:
        return {
            "local_only": requirements.local_only,
            "self_hosted_only": requirements.self_hosted_only,
            "tool_calling": requirements.tool_calling,
            "structured_output": requirements.structured_output,
            "streaming": requirements.streaming,
            "modalities": list(requirements.modalities),
            "reasoning": list(requirements.reasoning),
            "min_context_window": requirements.min_context_window,
        }

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
