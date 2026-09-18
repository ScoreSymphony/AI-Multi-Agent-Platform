"""Adapters from generic plugin extension registrations to platform-owned registries."""

from __future__ import annotations

from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.capabilities.registry import CapabilityRegistry
from ai_multi_agent_platform.connectors import ConnectorProvider, ConnectorService
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import ExtensionType
from .runtime import ExtensionRegistration


class CapabilityRegistryBinder:
    """Register plugin capability providers through the canonical capability registry."""

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


class ConnectorRegistryBinder:
    """Bind plugin connector providers through the canonical Connector owner."""

    def __init__(self, service: ConnectorService) -> None:
        self._service = service

    async def register(self, registration: ExtensionRegistration) -> None:
        if registration.spec.extension_type is not ExtensionType.CONNECTOR_PROVIDER:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION, "connector binder received wrong extension type"
            )
        if not isinstance(registration.instance, ConnectorProvider):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "connector_provider extension must implement ConnectorProvider",
            )
        provider = registration.instance
        definition = provider.definition
        had_runtime_provider = True
        try:
            self._service.registry.resolve(definition.connector_type_id, definition.version)
        except ContractError as exc:
            if exc.code is not ErrorCode.UNAVAILABLE:
                raise
            had_runtime_provider = False

        try:
            await self._service.register_provider(provider)
        # error-boundary: allow-broad-catch=cleanup rollback partial Connector registration
        except BaseException as register_error:
            if not had_runtime_provider:
                try:
                    current = self._service.registry.resolve(
                        definition.connector_type_id,
                        definition.version,
                    )
                except ContractError as lookup_error:
                    if lookup_error.code is not ErrorCode.UNAVAILABLE:
                        register_error.add_note(
                            "connector provider registration rollback could not inspect "
                            "runtime state"
                        )
                else:
                    if current is provider:
                        try:
                            self._service.registry.unregister(
                                definition.connector_type_id,
                                definition.version,
                            )
                        except ContractError:
                            register_error.add_note(
                                "connector provider registration rollback failed"
                            )
            raise

    async def unregister(self, registration: ExtensionRegistration) -> None:
        if not isinstance(registration.instance, ConnectorProvider):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "connector_provider extension must implement ConnectorProvider",
            )
        definition = registration.instance.definition
        self._service.registry.unregister(definition.connector_type_id, definition.version)
