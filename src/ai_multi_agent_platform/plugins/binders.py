"""Adapters from generic plugin extension registrations to platform-owned registries."""

from __future__ import annotations

from typing import Protocol, cast

from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.capabilities.registry import CapabilityRegistry
from ai_multi_agent_platform.connectors import ConnectorProvider, ConnectorService
from ai_multi_agent_platform.contracts import ModelProvider, Orchestrator
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.execution import Executor, ExecutorRegistry
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.orchestration import OrchestratorRegistry

from .models import ExtensionType
from .runtime import ExtensionRegistration


class _AgentOrchestratorMapper(Protocol):
    @property
    def adapter_id(self) -> str: ...

    async def map_agent(self, spec: object) -> object: ...


class _AgentOrchestratorMapperRegistry(Protocol):
    def register(self, mapper: _AgentOrchestratorMapper) -> None: ...

    def unregister(self, adapter_id: str) -> object: ...


def _as_agent_orchestrator_mapper(value: object) -> _AgentOrchestratorMapper | None:
    adapter_id = getattr(value, "adapter_id", None)
    map_agent = getattr(value, "map_agent", None)
    if not isinstance(adapter_id, str) or not callable(map_agent):
        return None
    return cast(_AgentOrchestratorMapper, value)


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


class OrchestratorRegistryBinder:
    """Bind Orchestrator extensions to canonical orchestration and Agent mapper registries."""

    def __init__(
        self,
        registry: OrchestratorRegistry,
        *,
        agent_mappers: object | None = None,
    ) -> None:
        self._registry = registry
        self._agent_mappers = (
            cast(_AgentOrchestratorMapperRegistry, agent_mappers)
            if agent_mappers is not None
            else None
        )

    async def register(self, registration: ExtensionRegistration) -> None:
        if registration.spec.extension_type is not ExtensionType.ORCHESTRATOR:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION, "orchestrator binder received wrong extension type"
            )
        if not isinstance(registration.instance, Orchestrator):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "orchestrator extension must implement the canonical Orchestrator contract",
            )
        orchestrator = registration.instance
        provider_id = orchestrator.descriptor.provider_id
        self._registry.register(provider_id, orchestrator)
        mapper = _as_agent_orchestrator_mapper(orchestrator)
        if self._agent_mappers is None or mapper is None:
            return
        if mapper.adapter_id != provider_id:
            self._registry.unregister(provider_id)
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Agent orchestrator mapper adapter_id must match provider_id",
            )
        try:
            self._agent_mappers.register(mapper)
        except ContractError as exc:
            try:
                self._registry.unregister(provider_id)
            except ContractError as rollback_error:
                exc.add_note(
                    "orchestrator registration rollback failed after Agent mapper registration error"
                )
                exc.add_note(str(rollback_error))
            raise

    async def unregister(self, registration: ExtensionRegistration) -> None:
        if not isinstance(registration.instance, Orchestrator):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "orchestrator extension must implement the canonical Orchestrator contract",
            )
        orchestrator = registration.instance
        provider_id = orchestrator.descriptor.provider_id
        removed_mapper = False
        mapper = _as_agent_orchestrator_mapper(orchestrator)
        if self._agent_mappers is not None and mapper is not None:
            self._agent_mappers.unregister(mapper.adapter_id)
            removed_mapper = True
        try:
            self._registry.unregister(provider_id)
        except ContractError as exc:
            if removed_mapper and self._agent_mappers is not None:
                try:
                    self._agent_mappers.register(mapper)
                except ContractError as rollback_error:
                    exc.add_note(
                        "Agent mapper rollback failed after orchestrator unregister error"
                    )
                    exc.add_note(str(rollback_error))
            raise


class ExecutorRegistryBinder:
    """Bind Executor extensions to the canonical execution registry."""

    def __init__(self, registry: ExecutorRegistry) -> None:
        self._registry = registry

    async def register(self, registration: ExtensionRegistration) -> None:
        if registration.spec.extension_type is not ExtensionType.EXECUTOR:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION, "executor binder received wrong extension type"
            )
        if not isinstance(registration.instance, Executor):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "executor extension must implement the canonical Executor contract",
            )
        executor = registration.instance
        self._registry.register(executor.descriptor.executor_id, executor)

    async def unregister(self, registration: ExtensionRegistration) -> None:
        if not isinstance(registration.instance, Executor):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "executor extension must implement the canonical Executor contract",
            )
        self._registry.unregister(registration.instance.descriptor.executor_id)


class ModelProviderRegistryBinder:
    """Bind provider implementations without creating configured model instances."""

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    async def register(self, registration: ExtensionRegistration) -> None:
        if registration.spec.extension_type is not ExtensionType.MODEL_PROVIDER:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION, "model-provider binder received wrong extension type"
            )
        if not isinstance(registration.instance, ModelProvider):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "model_provider extension must implement the canonical ModelProvider contract",
            )
        self._registry.register_provider(registration.instance)

    async def unregister(self, registration: ExtensionRegistration) -> None:
        if not isinstance(registration.instance, ModelProvider):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "model_provider extension must implement the canonical ModelProvider contract",
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
