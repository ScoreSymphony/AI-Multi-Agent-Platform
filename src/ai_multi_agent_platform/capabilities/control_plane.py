"""Read-only Control Plane projections for the canonical capability registry."""

from __future__ import annotations

from collections import defaultdict

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import Capability, JsonValue, ProviderDescriptor
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, json_object

from .registry import CapabilityRegistry
from .types import CapabilitySpec


class CapabilityResourceService:
    """Administrative capability inventory grouped by canonical capability ID."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        grouped: dict[str, list[CapabilitySpec]] = defaultdict(list)
        for capability in self._registry.inventory_capabilities(include_unavailable=True):
            grouped[capability.capability_id].append(capability)
        return tuple(
            _capability_resource(capability_id, tuple(grouped[capability_id]))
            for capability_id in sorted(grouped)
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        versions = tuple(
            capability
            for capability in self._registry.inventory_capabilities(include_unavailable=True)
            if capability.capability_id == resource_id
        )
        if not versions:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"capability not found: {resource_id}",
            )
        return _capability_resource(resource_id, versions)


class CapabilityProviderResourceService:
    """Public provider descriptors without exposing provider implementation objects."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _provider_resource(descriptor) for descriptor in self._registry.inventory_providers()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        for descriptor in self._registry.inventory_providers():
            if descriptor.provider_id == resource_id:
                return _provider_resource(descriptor)
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"capability provider not found: {resource_id}",
        )


def capability_resource_services(
    registry: CapabilityRegistry,
) -> dict[str, CapabilityResourceService | CapabilityProviderResourceService]:
    """Register #12 inventory through the generic versioned Control Plane seam."""

    return {
        "capabilities": CapabilityResourceService(registry),
        "capability-providers": CapabilityProviderResourceService(registry),
    }


def _capability_resource(
    capability_id: str,
    versions: tuple[CapabilitySpec, ...],
) -> dict[str, JsonValue]:
    version_resources: list[JsonValue] = [json_object(capability) for capability in versions]
    names = sorted({capability.name for capability in versions})
    return {
        "id": capability_id,
        "type": "capability",
        "name": names[0] if len(names) == 1 else capability_id,
        "version_count": len(versions),
        "available": any(capability.available for capability in versions),
        "versions": version_resources,
    }


def _provider_resource(descriptor: ProviderDescriptor) -> dict[str, JsonValue]:
    capabilities: list[JsonValue] = [
        _provider_capability_resource(capability) for capability in descriptor.capabilities
    ]
    return {
        "id": descriptor.provider_id,
        "type": "capability-provider",
        "provider_type": descriptor.provider_type,
        "contract_version": descriptor.contract_version,
        "supported_operations": list(descriptor.supported_operations),
        "capabilities": capabilities,
        "health": descriptor.health.value,
        "available": descriptor.available,
        "limits": dict(descriptor.limits),
        "resources": dict(descriptor.resources),
    }


def _provider_capability_resource(capability: Capability) -> dict[str, JsonValue]:
    return {
        "name": capability.name,
        "kind": capability.kind.value,
        "version": capability.version,
        "supported_operations": list(capability.supported_operations),
        "modalities": list(capability.modalities),
        "features": list(capability.features),
        "limits": dict(capability.limits),
        "attributes": dict(capability.attributes),
    }
