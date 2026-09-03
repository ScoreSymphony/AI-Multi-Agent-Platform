"""Bridge connector actions into the canonical capability registry/invocation pipeline."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilitySpec,
    CapabilityToolProvider,
    CredentialRequirement,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.security import ActorIdentity

from .service import ConnectorService

type ConnectorActorResolver = Callable[[OperationContext], ActorIdentity]


class ConnectorCapabilityProvider(CapabilityToolProvider):
    """One platform-owned capability provider routing actions to selected Connections."""

    def __init__(
        self,
        service: ConnectorService,
        *,
        actor_resolver: ConnectorActorResolver,
        provider_id: str = "platform.connector-bridge",
    ) -> None:
        self._service = service
        self._actor_resolver = actor_resolver
        self._provider_id = provider_id

    @property
    def descriptor(self) -> ProviderDescriptor:
        actions = sorted(
            {
                action
                for definition in self._service.registry.definitions()
                for action in definition.actions
            }
        )
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="connector_bridge",
            supported_operations=("invoke", "discover"),
            capabilities=tuple(
                Capability(
                    name=action,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                )
                for action in actions
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        definitions = self._service.registry.definitions()
        actions = sorted({action for definition in definitions for action in definition.actions})
        registrations: list[CapabilityRegistration] = []
        for action in actions:
            requires_credentials = any(
                definition.authentication_requirements and action in definition.actions
                for definition in definitions
            )
            spec = CapabilitySpec(
                capability_id=action,
                name=action,
                description="External connector action routed through a selected Connection.",
                version="1.0",
                input_schema={
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"connection_id": {"type": "string"}},
                    "required": ["connection_id"],
                    "additionalProperties": True,
                },
                tags=("connector", "external"),
                side_effects=SideEffectClassification.EXTERNAL,
                credential_requirement=(
                    CredentialRequirement.REQUIRED
                    if requires_credentials
                    else CredentialRequirement.NONE
                ),
                health=HealthStatus.HEALTHY,
                available=True,
            )
            registrations.append(
                CapabilityRegistration(
                    capability=spec,
                    provider_id=self._provider_id,
                    provider_tool_ref=action,
                    priority=100,
                )
            )
        return tuple(registrations)

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        arguments = invocation.arguments_json()
        connection_id = arguments.pop("connection_id", None)
        if not isinstance(connection_id, str) or not connection_id.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "connector capability requires a non-blank connection_id",
                provider_id=self._provider_id,
            )
        actor = self._actor_resolver(invocation.context)
        result = await self._service.invoke_action(
            connection_id,
            invocation.tool_ref,
            arguments,
            invocation_id=invocation.invocation_id,
            actor=actor,
            context=invocation.context,
        )
        return ToolResult(
            invocation_id=result.invocation_id,
            output=result.output,
            adapter_metadata=result.adapter_metadata,
        )
