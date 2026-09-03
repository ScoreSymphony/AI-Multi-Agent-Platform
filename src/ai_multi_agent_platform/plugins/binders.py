"""Adapters from generic plugin extension registrations to platform-owned registries."""

from __future__ import annotations

from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.capabilities.registry import CapabilityRegistry
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import ExtensionType
from .runtime import ExtensionRegistration


class CapabilityRegistryBinder:
    """Register plugin capability providers through the existing #12 registry."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    async def register(self, registration: ExtensionRegistration) -> None:
        if registration.spec.extension_type is not ExtensionType.CAPABILITY_PROVIDER:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION, "capability binder received wrong extension type"
            )
        if not isinstance(registration.instance, CapabilityToolProvider):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "capability_provider extension must implement CapabilityToolProvider",
            )
        await self._registry.register_provider(registration.instance)

    async def unregister(self, registration: ExtensionRegistration) -> None:
        if not isinstance(registration.instance, CapabilityToolProvider):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "capability_provider extension must implement CapabilityToolProvider",
            )
        self._registry.unregister_provider(registration.instance.descriptor.provider_id)
