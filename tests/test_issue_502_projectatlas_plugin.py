from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.capabilities import CapabilityRegistry, CapabilityToolProvider
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext, ToolInvocation
from ai_multi_agent_platform.plugins import (
    CapabilityRegistryBinder,
    ExtensionType,
    PluginContext,
    PluginHealth,
    PluginPermission,
    PluginRegistry,
    PluginState,
)
from ai_multi_agent_platform.repository_intelligence.capabilities import (
    RepositoryIntelligenceOperation,
)
from ai_multi_agent_platform.repository_intelligence.projectatlas import (
    PROJECTATLAS_ARCHIVE_SHA256,
    PROJECTATLAS_PLUGIN_ID,
    PROJECTATLAS_PROVIDER_ID,
    PROJECTATLAS_RUNTIME_VERSION,
    ProjectAtlasCandidatePlugin,
    ProjectAtlasRuntimeIdentity,
    projectatlas_candidate_manifest,
)


class _Probe:
    def __init__(self, identity: ProjectAtlasRuntimeIdentity) -> None:
        self.identity = identity
        self.calls: list[tuple[Path, Path]] = []

    async def probe(self, *, binary_path: Path, state_root: Path) -> ProjectAtlasRuntimeIdentity:
        self.calls.append((binary_path, state_root))
        return self.identity


def _plugin_registry(capabilities: CapabilityRegistry) -> PluginRegistry:
    return PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
        binders={
            ExtensionType.CAPABILITY_PROVIDER: CapabilityRegistryBinder(capabilities),
        },
    )


def _configuration(tmp_path: Path) -> dict[str, str]:
    state_root = tmp_path / "projectatlas-state"
    state_root.mkdir()
    return {
        "binary_path": str(tmp_path / "projectatlas"),
        "state_root": str(state_root),
    }


def test_projectatlas_manifest_is_experimental_and_fail_closed() -> None:
    manifest = projectatlas_candidate_manifest()

    assert manifest.plugin_id == PROJECTATLAS_PLUGIN_ID
    assert manifest.provenance.revision == f"v{PROJECTATLAS_RUNTIME_VERSION}"
    assert manifest.provenance.checksum == f"sha256:{PROJECTATLAS_ARCHIVE_SHA256}"
    assert manifest.capabilities == (
        RepositoryIntelligenceOperation.HEALTH.value,
        RepositoryIntelligenceOperation.INDEX_STATUS.value,
    )
    assert manifest.requested_permissions == frozenset(
        {
            PluginPermission.CAPABILITY_REGISTRATION,
            PluginPermission.WORKER_EXECUTION,
        }
    )
    assert PluginPermission.NETWORK_ACCESS not in manifest.requested_permissions
    assert PluginPermission.WORKSPACE_ACCESS not in manifest.requested_permissions
    assert PluginPermission.SECRET_CONSUMPTION not in manifest.requested_permissions
    metadata = manifest.extensions[0].metadata
    assert metadata["candidate_status"] == "experimental"
    assert metadata["source_operations_enabled"] is False
    assert metadata["network_isolation_verified"] is False


def test_projectatlas_plugin_registers_and_removes_only_status_capabilities(tmp_path: Path) -> None:
    capabilities = CapabilityRegistry()
    registry = _plugin_registry(capabilities)
    manifest = projectatlas_candidate_manifest()
    configuration = _configuration(tmp_path)
    probe = _Probe(ProjectAtlasRuntimeIdentity("projectatlas 0.4.5", True))
    runtime = ProjectAtlasCandidatePlugin(probe)

    registry.install(manifest, install_source="test:projectatlas-candidate")
    registry.configure(manifest.plugin_id, configuration)
    enabled = asyncio.run(
        registry.enable(
            manifest.plugin_id,
            runtime,
            granted_permissions=manifest.requested_permissions,
        )
    )

    assert enabled.state is PluginState.ENABLED
    assert enabled.health is PluginHealth.HEALTHY
    assert probe.calls == [
        (Path(configuration["binary_path"]), Path(configuration["state_root"]))
    ]
    assert {provider.provider_id for provider in capabilities.inventory_providers()} == {
        PROJECTATLAS_PROVIDER_ID
    }
    assert {capability.capability_id for capability in capabilities.inventory_capabilities()} == {
        RepositoryIntelligenceOperation.HEALTH.value,
        RepositoryIntelligenceOperation.INDEX_STATUS.value,
    }

    disabled = asyncio.run(registry.disable(manifest.plugin_id))
    assert disabled.state is PluginState.DISABLED
    assert capabilities.inventory_providers() == ()

    registry.remove(manifest.plugin_id)
    with pytest.raises(ContractError) as caught:
        registry.get(manifest.plugin_id)
    assert caught.value.code is ErrorCode.NOT_FOUND


def test_projectatlas_provider_rejects_source_operations_until_containment_passes(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)
    probe = _Probe(ProjectAtlasRuntimeIdentity("projectatlas 0.4.5", True))
    runtime = ProjectAtlasCandidatePlugin(probe)
    context = PluginContext(
        configuration=configuration,
        granted_permissions=projectatlas_candidate_manifest().requested_permissions,
    )

    registrations = asyncio.run(runtime.initialize(context))
    provider = registrations[0].instance
    assert isinstance(provider, CapabilityToolProvider)

    health = asyncio.run(
        provider.invoke(
            ToolInvocation(
                invocation_id="issue502-health",
                tool_ref=RepositoryIntelligenceOperation.HEALTH.value,
                arguments={},
                context=OperationContext(correlation_id="issue502"),
            )
        )
    )
    assert health.output["provider_id"] == PROJECTATLAS_PROVIDER_ID
    assert health.output["health"] == "healthy"

    status = asyncio.run(
        provider.invoke(
            ToolInvocation(
                invocation_id="issue502-status",
                tool_ref=RepositoryIntelligenceOperation.INDEX_STATUS.value,
                arguments={},
                context=OperationContext(correlation_id="issue502"),
            )
        )
    )
    assert status.output["indexed"] is False
    assert status.output["freshness"] == "unknown"
    assert status.output["rebuild_required"] is True

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            provider.invoke(
                ToolInvocation(
                    invocation_id="issue502-source",
                    tool_ref=RepositoryIntelligenceOperation.SOURCE_SLICE.value,
                    arguments={"repository_id": "repo", "path": "README.md"},
                    context=OperationContext(correlation_id="issue502"),
                )
            )
        )
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_projectatlas_plugin_enable_fails_closed_when_probe_rejects_runtime(tmp_path: Path) -> None:
    capabilities = CapabilityRegistry()
    registry = _plugin_registry(capabilities)
    manifest = projectatlas_candidate_manifest()
    configuration = _configuration(tmp_path)
    runtime = ProjectAtlasCandidatePlugin(
        _Probe(
            ProjectAtlasRuntimeIdentity(
                "projectatlas 0.4.6",
                False,
                "runtime version mismatch",
            )
        )
    )

    registry.install(manifest)
    registry.configure(manifest.plugin_id, configuration)
    with pytest.raises(ContractError) as caught:
        asyncio.run(
            registry.enable(
                manifest.plugin_id,
                runtime,
                granted_permissions=manifest.requested_permissions,
            )
        )

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    snapshot = registry.get(manifest.plugin_id)
    assert snapshot.state is PluginState.FAILED
    assert snapshot.health is PluginHealth.UNAVAILABLE
    assert capabilities.inventory_providers() == ()


def test_projectatlas_plugin_rejects_relative_runtime_paths(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    runtime = ProjectAtlasCandidatePlugin(
        _Probe(ProjectAtlasRuntimeIdentity("projectatlas 0.4.5", True))
    )

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            runtime.initialize(
                PluginContext(
                    configuration={
                        "binary_path": "relative/projectatlas",
                        "state_root": str(state_root),
                    },
                    granted_permissions=projectatlas_candidate_manifest().requested_permissions,
                )
            )
        )
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
