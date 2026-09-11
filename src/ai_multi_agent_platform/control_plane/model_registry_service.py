"""Model/provider registry operations behind the Control Plane façade."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import ModelProvider
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import ModelConfiguration, ModelRegistry

from .models import json_object


class ControlPlaneModelRegistry:
    """Own northbound model/provider projections without owning authorization."""

    def __init__(self, registry: ModelRegistry | None) -> None:
        self._registry = registry

    def list_providers(self) -> list[dict[str, JsonValue]]:
        registry = self.require_registry()
        return [_model_provider_resource(registry, provider) for provider in registry.list_providers()]

    def get_provider(self, provider_id: str) -> dict[str, JsonValue]:
        registry = self.require_registry()
        return _model_provider_resource(registry, registry.get_provider(provider_id))

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> dict[str, JsonValue]:
        registry = self.require_registry()
        registry.set_provider_enabled(provider_id, enabled)
        return _model_provider_resource(registry, registry.get_provider(provider_id))

    async def refresh_provider_health(self, provider_id: str) -> dict[str, JsonValue]:
        registry = self.require_registry()
        await registry.refresh_health(provider_id)
        return _model_provider_resource(registry, registry.get_provider(provider_id))

    def list_models(self) -> list[dict[str, JsonValue]]:
        registry = self.require_registry()
        return [_model_resource(registry, config) for config in registry.list_models()]

    def get_model(self, model_id_or_alias: str) -> dict[str, JsonValue]:
        registry = self.require_registry()
        return _model_resource(registry, registry.get_model(model_id_or_alias))

    def set_model_enabled(self, model_id_or_alias: str, enabled: bool) -> dict[str, JsonValue]:
        registry = self.require_registry()
        return _model_resource(registry, registry.set_enabled(model_id_or_alias, enabled))

    def require_registry(self) -> ModelRegistry:
        if self._registry is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical model registry is not configured",
                retryable=True,
                details={"resource": "models"},
            )
        return self._registry


def _model_provider_resource(
    registry: ModelRegistry,
    provider: ModelProvider,
) -> dict[str, JsonValue]:
    descriptor = provider.descriptor
    return {
        "id": descriptor.provider_id,
        "type": "model-provider",
        "provider_type": descriptor.provider_type,
        "contract_version": descriptor.contract_version,
        "supported_operations": list(descriptor.supported_operations),
        "capabilities": [json_object(item) for item in descriptor.capabilities],
        "health": registry.provider_health(descriptor.provider_id).value,
        "enabled": registry.provider_enabled(descriptor.provider_id),
        "available": descriptor.available,
        "limits": dict(descriptor.limits),
        "resources": dict(descriptor.resources),
        "adapter_metadata": [json_object(item) for item in descriptor.adapter_metadata],
    }


def _model_resource(
    registry: ModelRegistry,
    config: ModelConfiguration,
) -> dict[str, JsonValue]:
    resource = json_object(config)
    resource["id"] = config.config_id
    resource["type"] = "model"
    resource["effective_health"] = registry.effective_health(config).value
    return resource
