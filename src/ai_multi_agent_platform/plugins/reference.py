"""Deterministic reference plugin proving the issue #20 lifecycle."""

from __future__ import annotations

from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.capabilities.types import (
    CapabilityRegistration,
    CapabilitySpec,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)

from .models import (
    ExtensionType,
    PluginExtensionSpec,
    PluginHealth,
    PluginHealthReport,
    PluginManifest,
    PluginPermission,
    PluginProvenance,
    VersionRange,
)
from .runtime import ExtensionRegistration, PluginContext

REFERENCE_PLUGIN_ID = "reference.capability-plugin"
REFERENCE_EXTENSION_ID = "capability-provider.reference-echo"
REFERENCE_CAPABILITY_ID = "plugin.echo"
REFERENCE_TOOL_REF = "plugin.reference.echo"


def reference_manifest() -> PluginManifest:
    return PluginManifest(
        plugin_id=REFERENCE_PLUGIN_ID,
        name="Reference capability plugin",
        description="Deterministic capability provider used to prove plugin lifecycle semantics.",
        plugin_version="1.0.0",
        author="ScoreSymphony",
        provenance=PluginProvenance(
            source="bundled-reference",
            license="MIT",
            source_repository="https://github.com/ScoreSymphony/AI-Multi-Agent-Platform",
            trust_source="platform-source",
        ),
        supported_platform=VersionRange(minimum="0.0.1", maximum="0.0.1"),
        extensions=(
            PluginExtensionSpec(
                extension_id=REFERENCE_EXTENSION_ID,
                extension_type=ExtensionType.CAPABILITY_PROVIDER,
                interface_version="1.0",
                entrypoint="ai_multi_agent_platform.plugins.reference:ReferenceCapabilityPlugin",
                metadata={"capability_id": REFERENCE_CAPABILITY_ID},
            ),
        ),
        capabilities=(REFERENCE_CAPABILITY_ID,),
        requested_permissions=frozenset({PluginPermission.CAPABILITY_REGISTRATION}),
        configuration_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"prefix": {"type": "string"}},
            "additionalProperties": False,
        },
    )


class ReferenceCapabilityPlugin:
    def __init__(
        self,
        *,
        health: PluginHealth = PluginHealth.HEALTHY,
        fail_initialize: bool = False,
    ) -> None:
        self._health = health
        self._fail_initialize = fail_initialize
        self._provider: _ReferenceEchoProvider | None = None

    async def initialize(self, context: PluginContext) -> tuple[ExtensionRegistration, ...]:
        if self._fail_initialize:
            raise ContractError(
                ErrorCode.PERMANENT_FAILURE, "reference plugin initialization failed"
            )
        prefix = context.configuration.get("prefix", "")
        if not isinstance(prefix, str):
            raise ContractError(ErrorCode.INVALID_CONFIGURATION, "prefix must be a string")
        self._provider = _ReferenceEchoProvider(prefix=prefix)
        return (
            ExtensionRegistration(
                spec=reference_manifest().extensions[0],
                instance=self._provider,
            ),
        )

    async def health(self) -> PluginHealthReport:
        return PluginHealthReport(self._health)

    async def shutdown(self) -> None:
        self._provider = None


class _ReferenceEchoProvider(CapabilityToolProvider):
    def __init__(self, *, prefix: str) -> None:
        self._prefix = prefix

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="plugin.reference.echo",
            provider_type="plugin",
            supported_operations=("invoke", "discover"),
            capabilities=(
                Capability(
                    name=REFERENCE_CAPABILITY_ID,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id=REFERENCE_CAPABILITY_ID,
                    name="Plugin Echo",
                    description="Return a configured prefix followed by the supplied message.",
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
                    tags=("plugin", "reference", "deterministic"),
                    side_effects=SideEffectClassification.NONE,
                    health=HealthStatus.HEALTHY,
                    available=True,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref=REFERENCE_TOOL_REF,
                priority=100,
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        if invocation.tool_ref != REFERENCE_TOOL_REF:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"reference plugin does not expose tool {invocation.tool_ref!r}",
            )
        arguments = invocation.arguments_json()
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"message": f"{self._prefix}{arguments['message']}"},
        )
