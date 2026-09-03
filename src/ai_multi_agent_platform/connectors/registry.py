"""Runtime registry for replaceable connector providers."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import ProviderDescriptor

from .models import ConnectorDefinition
from .provider import ConnectorProvider


class ConnectorRegistry:
    """Resolve connector type/version to an implementation without leaking adapter types."""

    def __init__(self) -> None:
        self._providers: dict[tuple[str, str], ConnectorProvider] = {}

    def register(self, provider: ConnectorProvider) -> ConnectorDefinition:
        definition = provider.definition
        key = (definition.connector_type_id, definition.version)
        if key in self._providers:
            raise ContractError(
                ErrorCode.CONFLICT,
                (
                    "connector provider already registered for "
                    f"{definition.connector_type_id!r} version {definition.version!r}"
                ),
            )
        self._providers[key] = provider
        return definition

    def unregister(self, connector_type_id: str, version: str) -> None:
        key = (connector_type_id, version)
        if key not in self._providers:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"connector provider is not registered: {connector_type_id!r} {version!r}",
            )
        del self._providers[key]

    def resolve(self, connector_type_id: str, version: str) -> ConnectorProvider:
        try:
            return self._providers[(connector_type_id, version)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"connector implementation unavailable: {connector_type_id!r} {version!r}",
            ) from exc

    def definitions(self) -> tuple[ConnectorDefinition, ...]:
        return tuple(
            self._providers[key].definition
            for key in sorted(self._providers, key=lambda item: (item[0], item[1]))
        )

    def providers(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(
            self._providers[key].descriptor
            for key in sorted(self._providers, key=lambda item: (item[0], item[1]))
        )
