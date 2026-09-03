"""Read-only Control Plane projection for issue #13 data providers."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import Capability, JsonValue, ProviderDescriptor
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .registry import DataProviderSet

DATA_PROVIDER_COLLECTION = "data-providers"


class DataProviderResourceService:
    """Administrative health/metadata inventory for File, Memory and Knowledge providers."""

    def __init__(self, providers: DataProviderSet) -> None:
        self._providers = providers
        _validate_unique_provider_ids(self._descriptors())

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _provider_resource(role, descriptor)
            for role, descriptor in self._descriptors()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        for role, descriptor in self._descriptors():
            if descriptor.provider_id == resource_id:
                return _provider_resource(role, descriptor)
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"data provider not found: {resource_id}",
        )

    def _descriptors(self) -> tuple[tuple[str, ProviderDescriptor], ...]:
        return (
            ("file", self._providers.files.descriptor),
            ("memory", self._providers.memory.descriptor),
            ("knowledge", self._providers.knowledge.descriptor),
        )


def data_resource_services(providers: DataProviderSet) -> dict[str, DataProviderResourceService]:
    """Register the #13 administrative inventory through the generic Control Plane seam."""

    return {DATA_PROVIDER_COLLECTION: DataProviderResourceService(providers)}


def _provider_resource(role: str, descriptor: ProviderDescriptor) -> dict[str, JsonValue]:
    capabilities: list[JsonValue] = [
        _capability_resource(capability) for capability in descriptor.capabilities
    ]
    return {
        "id": descriptor.provider_id,
        "type": "data-provider",
        "role": role,
        "provider_type": descriptor.provider_type,
        "contract_version": descriptor.contract_version,
        "supported_operations": list(descriptor.supported_operations),
        "capabilities": capabilities,
        "health": descriptor.health.value,
        "available": descriptor.available,
        "limits": dict(descriptor.limits),
        "resources": dict(descriptor.resources),
    }


def _capability_resource(capability: Capability) -> dict[str, JsonValue]:
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


def _validate_unique_provider_ids(
    descriptors: tuple[tuple[str, ProviderDescriptor], ...],
) -> None:
    provider_ids = [descriptor.provider_id for _, descriptor in descriptors]
    if len(set(provider_ids)) != len(provider_ids):
        raise ValueError("File, Memory and Knowledge providers must use unique provider IDs")
