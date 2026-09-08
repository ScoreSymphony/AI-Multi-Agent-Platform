"""Experimental ProjectAtlas plugin shell for issue #502.

This module deliberately exposes only provider health and index-status capabilities. The pinned
ProjectAtlas runtime passed the contained functional pilot, but repository/source/graph operations
remain disabled until network-egress containment and the real read-only adapter boundary are
verified. The plugin therefore proves #20 install/enable/disable semantics without granting the
candidate repository, Workspace, secret, or network authority.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from ai_multi_agent_platform.capabilities import CapabilityRegistration, CapabilityToolProvider
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.plugins.models import (
    ExtensionType,
    PluginExtensionSpec,
    PluginHealth,
    PluginHealthReport,
    PluginManifest,
    PluginPermission,
    PluginProvenance,
    VersionRange,
)
from ai_multi_agent_platform.plugins.runtime import ExtensionRegistration, PluginContext

from .capabilities import (
    RepositoryIntelligenceOperation,
    repository_intelligence_capability_specs,
)
from .models import RepositoryIntelligenceFreshness

PROJECTATLAS_PLUGIN_ID = "repository-intelligence.projectatlas"
PROJECTATLAS_EXTENSION_ID = "capability-provider.repository-intelligence.projectatlas"
PROJECTATLAS_PROVIDER_ID = "plugin.repository-intelligence.projectatlas"
PROJECTATLAS_RUNTIME_VERSION = "0.4.5"
PROJECTATLAS_PLUGIN_VERSION = "0.1.0"
PROJECTATLAS_ARCHIVE_SHA256 = "e22ac7f9e37b1eb49929e4a8ad72c51affd2668731832652ee4783e20a8fabd9"
_PROJECTATLAS_CAPABILITIES = (
    RepositoryIntelligenceOperation.HEALTH,
    RepositoryIntelligenceOperation.INDEX_STATUS,
)
_PROBE_TIMEOUT_SECONDS = 10.0
_MAX_VERSION_OUTPUT_BYTES = 4096


@dataclass(frozen=True, slots=True)
class ProjectAtlasRuntimeIdentity:
    """Content-free runtime probe result used before capability registration."""

    version: str
    available: bool
    detail: str | None = None


class ProjectAtlasRuntimeProbe(Protocol):
    """Probe the configured runtime without touching repository or Workspace content."""

    async def probe(
        self, *, binary_path: Path, state_root: Path
    ) -> ProjectAtlasRuntimeIdentity: ...


class LocalProjectAtlasRuntimeProbe:
    """Run only ``projectatlas --version`` with a secret-minimal environment."""

    async def probe(self, *, binary_path: Path, state_root: Path) -> ProjectAtlasRuntimeIdentity:
        return await asyncio.to_thread(
            self._probe_sync,
            binary_path=binary_path,
            state_root=state_root,
        )

    def _probe_sync(
        self,
        *,
        binary_path: Path,
        state_root: Path,
    ) -> ProjectAtlasRuntimeIdentity:
        if not binary_path.is_file():
            return ProjectAtlasRuntimeIdentity("", False, "configured runtime does not exist")
        if not os.access(binary_path, os.X_OK):
            return ProjectAtlasRuntimeIdentity("", False, "configured runtime is not executable")
        if not state_root.is_dir():
            return ProjectAtlasRuntimeIdentity("", False, "configured state root does not exist")

        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(state_root),
            "XDG_CACHE_HOME": str(state_root / "xdg-cache"),
            "XDG_CONFIG_HOME": str(state_root / "xdg-config"),
            "XDG_DATA_HOME": str(state_root / "xdg-data"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        try:
            completed = subprocess.run(
                [str(binary_path), "--version"],
                cwd=state_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ProjectAtlasRuntimeIdentity("", False, f"runtime probe failed: {exc}")

        encoded = completed.stdout.encode("utf-8", errors="replace")
        if len(encoded) > _MAX_VERSION_OUTPUT_BYTES:
            return ProjectAtlasRuntimeIdentity("", False, "runtime version output exceeded bound")
        version = completed.stdout.strip()
        if completed.returncode != 0:
            return ProjectAtlasRuntimeIdentity(
                version,
                False,
                f"runtime version probe exited with status {completed.returncode}",
            )
        if version != f"projectatlas {PROJECTATLAS_RUNTIME_VERSION}":
            return ProjectAtlasRuntimeIdentity(
                version,
                False,
                (f"runtime version mismatch: expected projectatlas {PROJECTATLAS_RUNTIME_VERSION}"),
            )
        return ProjectAtlasRuntimeIdentity(version, True)


def projectatlas_candidate_manifest() -> PluginManifest:
    """Return the experimental #20 manifest for the pinned ProjectAtlas candidate."""

    return PluginManifest(
        plugin_id=PROJECTATLAS_PLUGIN_ID,
        name="ProjectAtlas repository-intelligence candidate",
        description=(
            "Experimental ProjectAtlas v0.4.5 capability shell. Source and graph operations remain "
            "disabled until network containment and adapter provenance gates pass."
        ),
        plugin_version=PROJECTATLAS_PLUGIN_VERSION,
        author="ScoreSymphony",
        provenance=PluginProvenance(
            source="bundled-experimental-adapter",
            license="MIT",
            source_repository="https://github.com/styler-ai/ProjectAtlas",
            revision=f"v{PROJECTATLAS_RUNTIME_VERSION}",
            checksum=f"sha256:{PROJECTATLAS_ARCHIVE_SHA256}",
            trust_source="issue-502-contained-functional-pilot",
            local_modifications=(
                "ScoreSymphony adapter only; the upstream ProjectAtlas executable is not bundled"
            ),
        ),
        supported_platform=VersionRange(minimum="0.0.1", maximum="0.0.1"),
        extensions=(
            PluginExtensionSpec(
                extension_id=PROJECTATLAS_EXTENSION_ID,
                extension_type=ExtensionType.CAPABILITY_PROVIDER,
                interface_version="1.0",
                entrypoint=(
                    "ai_multi_agent_platform.repository_intelligence.projectatlas:"
                    "ProjectAtlasCandidatePlugin"
                ),
                metadata={
                    "candidate_status": "experimental",
                    "runtime": "projectatlas",
                    "runtime_version": PROJECTATLAS_RUNTIME_VERSION,
                    "source_operations_enabled": False,
                    "network_isolation_verified": False,
                },
            ),
        ),
        capabilities=tuple(operation.value for operation in _PROJECTATLAS_CAPABILITIES),
        requested_permissions=frozenset(
            {
                PluginPermission.CAPABILITY_REGISTRATION,
                PluginPermission.WORKER_EXECUTION,
            }
        ),
        configuration_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "binary_path": {"type": "string", "minLength": 1},
                "state_root": {"type": "string", "minLength": 1},
            },
            "required": ["binary_path", "state_root"],
            "additionalProperties": False,
        },
    )


class ProjectAtlasCandidatePlugin:
    """#20 runtime shell that fails closed before registering any source capability."""

    def __init__(self, probe: ProjectAtlasRuntimeProbe | None = None) -> None:
        self._probe = probe or LocalProjectAtlasRuntimeProbe()
        self._provider: _ProjectAtlasStatusProvider | None = None
        self._health = PluginHealth.UNKNOWN
        self._health_detail: str | None = None

    async def initialize(self, context: PluginContext) -> tuple[ExtensionRegistration, ...]:
        binary_path = _configured_absolute_path(context.configuration, "binary_path")
        state_root = _configured_absolute_path(context.configuration, "state_root")
        identity = await self._probe.probe(binary_path=binary_path, state_root=state_root)
        if not identity.available:
            self._health = PluginHealth.UNAVAILABLE
            self._health_detail = identity.detail or "ProjectAtlas runtime is unavailable"
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                self._health_detail,
                provider_id=PROJECTATLAS_PROVIDER_ID,
            )

        self._provider = _ProjectAtlasStatusProvider(
            runtime_identity=identity,
            state_root=state_root,
        )
        self._health = PluginHealth.HEALTHY
        self._health_detail = (
            "functional pilot passed; source capabilities remain gated on network containment"
        )
        return (
            ExtensionRegistration(
                spec=projectatlas_candidate_manifest().extensions[0],
                instance=self._provider,
            ),
        )

    async def health(self) -> PluginHealthReport:
        return PluginHealthReport(self._health, self._health_detail)

    async def shutdown(self) -> None:
        self._provider = None
        self._health = PluginHealth.UNKNOWN
        self._health_detail = None


class _ProjectAtlasStatusProvider(CapabilityToolProvider):
    def __init__(
        self,
        *,
        runtime_identity: ProjectAtlasRuntimeIdentity,
        state_root: Path,
    ) -> None:
        self._runtime_identity = runtime_identity
        self._state_root = state_root

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=PROJECTATLAS_PROVIDER_ID,
            provider_type="repository_intelligence",
            supported_operations=("discover", "invoke"),
            capabilities=tuple(
                Capability(
                    name=operation.value,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                )
                for operation in _PROJECTATLAS_CAPABILITIES
            ),
            health=HealthStatus.HEALTHY,
            available=True,
            resources={
                "runtime": "projectatlas",
                "runtime_version": PROJECTATLAS_RUNTIME_VERSION,
                "state_class": "derived_index",
                "source_operations_enabled": False,
                "network_isolation_verified": False,
            },
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        specs = {spec.capability_id: spec for spec in repository_intelligence_capability_specs()}
        return tuple(
            CapabilityRegistration(
                capability=replace(
                    specs[operation.value],
                    health=HealthStatus.HEALTHY,
                    available=True,
                ),
                provider_id=PROJECTATLAS_PROVIDER_ID,
                provider_tool_ref=operation.value,
                priority=50,
            )
            for operation in _PROJECTATLAS_CAPABILITIES
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        try:
            operation = RepositoryIntelligenceOperation(invocation.tool_ref)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"ProjectAtlas candidate does not expose {invocation.tool_ref!r}",
                provider_id=PROJECTATLAS_PROVIDER_ID,
            ) from exc

        if operation is RepositoryIntelligenceOperation.HEALTH:
            output: dict[str, JsonValue] = {
                "provider_id": PROJECTATLAS_PROVIDER_ID,
                "health": HealthStatus.HEALTHY.value,
                "available": True,
            }
        elif operation is RepositoryIntelligenceOperation.INDEX_STATUS:
            output = {
                "provider_id": PROJECTATLAS_PROVIDER_ID,
                "indexed": False,
                "state_class": "derived_index",
                "freshness": RepositoryIntelligenceFreshness.UNKNOWN.value,
                "rebuild_required": True,
                "notes": (
                    "ProjectAtlas v0.4.5 runtime verified; repository indexing remains disabled "
                    "until egress containment and canonical source provenance gates pass"
                ),
            }
        else:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                (
                    "ProjectAtlas source/graph capabilities are intentionally disabled while "
                    "network containment remains unverified"
                ),
                provider_id=PROJECTATLAS_PROVIDER_ID,
            )
        return ToolResult(invocation_id=invocation.invocation_id, output=output)


def _configured_absolute_path(configuration: dict[str, JsonValue], key: str) -> Path:
    value = configuration.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"ProjectAtlas configuration {key!r} must be a non-blank string",
        )
    path = Path(value)
    if not path.is_absolute():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"ProjectAtlas configuration {key!r} must be an absolute path",
        )
    return path
