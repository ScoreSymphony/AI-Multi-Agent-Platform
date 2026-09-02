"""Deterministic native reference capability."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)

from .provider import CapabilityToolProvider
from .types import CapabilityRegistration, CapabilitySpec, SideEffectClassification

ECHO_CAPABILITY_ID = "tool.echo"
ECHO_TOOL_REF = "native.echo"


class NativeEchoProvider(CapabilityToolProvider):
    """Small deterministic provider used by contract tests and bootstrap deployments."""

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="native.reference",
            provider_type="native",
            supported_operations=("invoke", "discover"),
            capabilities=(
                Capability(
                    name=ECHO_CAPABILITY_ID,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        spec = CapabilitySpec(
            capability_id=ECHO_CAPABILITY_ID,
            name="Echo",
            description="Return the supplied message unchanged.",
            version="1.0",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            output_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            tags=("reference", "deterministic"),
            side_effects=SideEffectClassification.NONE,
            health=HealthStatus.HEALTHY,
            available=True,
        )
        return (
            CapabilityRegistration(
                capability=spec,
                provider_id=self.descriptor.provider_id,
                provider_tool_ref=ECHO_TOOL_REF,
                priority=100,
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        if invocation.tool_ref != ECHO_TOOL_REF:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"native reference provider does not expose tool {invocation.tool_ref!r}",
                provider_id=self.descriptor.provider_id,
            )
        arguments = invocation.arguments_json()
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"message": arguments["message"]},
        )
