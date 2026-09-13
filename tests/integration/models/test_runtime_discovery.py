from __future__ import annotations

from ai_multi_agent_platform.contracts import HealthStatus, ProviderDescriptor
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import ModelConfiguration, ModelLocation, ModelRegistry
from ai_multi_agent_platform.onboarding import (
    ComponentAvailability,
    ComponentCategory,
    SetupMode,
    SingleNodeComponentDiscoverySource,
)
from ai_multi_agent_platform.testing import FakeModelProvider


class LocalProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="local-provider",
        provider_type="openai-compatible",
        health=HealthStatus.HEALTHY,
    )


class RemoteProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="remote-provider",
        provider_type="remote-api",
        health=HealthStatus.HEALTHY,
    )


def _by_key(source: SingleNodeComponentDiscoverySource) -> dict[tuple[str, str], object]:
    return {
        (component.category.value, component.component_id): component
        for component in source.discover()
    }


def test_single_node_discovery_reports_only_proven_baseline_components() -> None:
    source = SingleNodeComponentDiscoverySource(ModelRegistry())

    components = _by_key(source)

    assert ("orchestrator", "reference-orchestrator") in components
    assert ("executor", "reference-executor") in components
    assert ("tools_mcp", "native-capabilities") in components
    assert ("storage", "local-files") in components
    assert ("compute", "local-node") in components
    assert ("compute", "distributed-workers") not in components
    assert not any(key[0] == ComponentCategory.MEMORY_KNOWLEDGE.value for key in components)


def test_model_provider_discovery_tracks_registry_add_and_remove() -> None:
    registry = ModelRegistry()
    source = SingleNodeComponentDiscoverySource(registry)
    provider = LocalProvider()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="local-model",
            display_name="Local model",
            provider_id=provider.descriptor.provider_id,
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
        )
    )

    present = _by_key(source)
    discovered = present[("model_provider", "local-provider")]
    assert discovered.availability is ComponentAvailability.AVAILABLE
    assert SetupMode.LOCAL in discovered.recommended_modes
    assert discovered.metadata["model_locations"] == ["local"]

    registry.unregister_provider("local-provider")

    absent = _by_key(source)
    assert ("model_provider", "local-provider") not in absent


def test_remote_only_provider_is_visible_but_not_recommended_for_auto_or_local() -> None:
    registry = ModelRegistry()
    provider = RemoteProvider()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="remote-model",
            display_name="Remote model",
            provider_id=provider.descriptor.provider_id,
            location=ModelLocation.REMOTE,
            health=HealthStatus.HEALTHY,
        )
    )
    source = SingleNodeComponentDiscoverySource(registry)

    discovered = _by_key(source)[("model_provider", "remote-provider")]

    assert discovered.recommended_modes == frozenset({SetupMode.ADVANCED})
    assert discovered.metadata["model_locations"] == ["remote"]


def test_unknown_provider_health_is_not_reported_available() -> None:
    registry = ModelRegistry()
    provider = LocalProvider()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="local-model",
            display_name="Local model",
            provider_id=provider.descriptor.provider_id,
            location=ModelLocation.LOCAL,
        )
    )
    registry._provider_health[provider.descriptor.provider_id] = HealthStatus.UNKNOWN
    source = SingleNodeComponentDiscoverySource(registry)

    discovered = _by_key(source)[("model_provider", "local-provider")]

    assert discovered.availability is ComponentAvailability.UNAVAILABLE
    assert discovered.metadata["health"] == "unknown"


def test_distributed_compute_becomes_available_only_with_usable_worker() -> None:
    registry = DistributedRegistry()
    runtime = DistributedRuntime(registry)
    source = SingleNodeComponentDiscoverySource(ModelRegistry(), distributed_runtime=runtime)

    empty = _by_key(source)[("compute", "distributed-workers")]
    assert empty.availability is ComponentAvailability.UNAVAILABLE
    assert "compute:multi_node" not in source.environment().capabilities

    node = NodeRecord(
        node_id=new_id("node"),
        display_name="issue-799-node",
        resources=ResourceSnapshot(
            cpu_cores_total=4,
            cpu_cores_available=4,
            ram_total_bytes=8_000,
            ram_available_bytes=8_000,
            storage_total_bytes=20_000,
            storage_available_bytes=20_000,
        ),
        os_name="Linux",
        architecture="x86_64",
        supported_runtimes=("container",),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
    )
    registry.register(RegistrationRequest(node=node, workers=(worker,)))

    available = _by_key(source)[("compute", "distributed-workers")]
    environment = source.environment()

    assert available.availability is ComponentAvailability.AVAILABLE
    assert "compute:multi_node" in environment.capabilities
    assert "os:linux" in environment.capabilities
    assert "arch:x86_64" in environment.capabilities
    assert "runtime:container" in environment.capabilities
