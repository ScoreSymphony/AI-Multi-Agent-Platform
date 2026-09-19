"""Canonical Plugin runtime wrapper for the optional Hermes orchestrator adapter.

Marketplace distribution may install the manifest, but executable code is supplied only
through the trusted PluginSource composed by the platform.  The manifest entrypoint is
metadata and is never dynamically imported by the Marketplace.
"""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.plugins import (
    ExtensionRegistration,
    ExtensionType,
    PluginContext,
    PluginExtensionSpec,
    PluginHealth,
    PluginHealthReport,
    PluginManifest,
    PluginPermission,
    PluginProvenance,
    VersionRange,
)

from .hermes import HermesOrchestrator
from .hermes_config import (
    HERMES_CONFIGURATION_SCHEMA,
    HERMES_PINNED_REVISION,
    HERMES_UPSTREAM_REPOSITORY,
    HermesAdapterConfig,
)

HERMES_PLUGIN_ID = "hermes.orchestrator-plugin"
HERMES_EXTENSION_ID = "orchestrator.hermes"
HERMES_PLUGIN_VERSION = "1.0.0"


def hermes_plugin_manifest() -> PluginManifest:
    """Return the immutable platform-owned Plugin contract for Hermes."""

    configuration_schema = cast(
        dict[str, JsonValue],
        deepcopy(dict(HERMES_CONFIGURATION_SCHEMA.json_schema)),
    )
    return PluginManifest(
        plugin_id=HERMES_PLUGIN_ID,
        name="Hermes",
        description=(
            "Replaceable Hermes orchestrator adapter backed by the governed external "
            "Hermes API server."
        ),
        plugin_version=HERMES_PLUGIN_VERSION,
        author="ScoreSymphony",
        provenance=PluginProvenance(
            source="platform-owned-adapter",
            license="MIT",
            source_repository=HERMES_UPSTREAM_REPOSITORY,
            revision=HERMES_PINNED_REVISION,
            trust_source="governed-upstream-pin",
        ),
        supported_platform=VersionRange(minimum="0.0.1"),
        extensions=(
            PluginExtensionSpec(
                extension_id=HERMES_EXTENSION_ID,
                extension_type=ExtensionType.ORCHESTRATOR,
                interface_version="1.0",
                entrypoint=(
                    "ai_multi_agent_platform.adapters.hermes_plugin:HermesOrchestratorPlugin"
                ),
                metadata={
                    "agent_support": True,
                    "team_support": True,
                    "planning": True,
                    "replanning": True,
                    "cancellation": True,
                    "reconciliation": True,
                },
            ),
        ),
        requested_permissions=frozenset(
            {
                PluginPermission.NETWORK_ACCESS,
                PluginPermission.SECRET_CONSUMPTION,
            }
        ),
        configuration_schema=configuration_schema,
        optional_external_services=("hermes-api-server",),
        ui_metadata={
            "category": "orchestrator",
            "management_surface": "plugins",
        },
    )


class HermesOrchestratorPlugin:
    """Explicit runtime factory target for the canonical Hermes PluginSource."""

    def __init__(self) -> None:
        self._orchestrator: HermesOrchestrator | None = None

    async def initialize(self, context: PluginContext) -> tuple[ExtensionRegistration, ...]:
        try:
            config = HermesAdapterConfig.from_mapping(context.configuration)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"invalid Hermes plugin configuration: {exc}",
            ) from exc
        if not config.enabled:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Hermes plugin configuration must set enabled=true before activation",
            )
        self._orchestrator = HermesOrchestrator(config)
        return (
            ExtensionRegistration(
                spec=hermes_plugin_manifest().extensions[0],
                instance=self._orchestrator,
            ),
        )

    async def health(self) -> PluginHealthReport:
        if self._orchestrator is None:
            return PluginHealthReport(PluginHealth.UNKNOWN)
        health = await self._orchestrator.health()
        return PluginHealthReport(PluginHealth(health.value))

    async def shutdown(self) -> None:
        self._orchestrator = None
