"""Production-shaped discovery sources for issue #799 first-run component setup."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.distributed.models import NodeStatus, WorkerStatus
from ai_multi_agent_platform.models import ModelLocation, ModelRegistry

from .components import (
    CompatibilityEnvironment,
    ComponentAvailability,
    ComponentCategory,
    ComponentLifecycle,
    DiscoveredComponent,
    SetupMode,
)


class SingleNodeComponentDiscoverySource:
    """Discover only components proven by the active single-node composition.

    Baseline reference components are reported because ``build_single_node_deployment`` always
    constructs them. Model providers and distributed compute are read dynamically from their
    canonical registries, so provider/node removal is reflected without migrating stored profiles.
    """

    def __init__(
        self,
        models: ModelRegistry,
        *,
        distributed_runtime: DistributedRuntime | None = None,
    ) -> None:
        self.models = models
        self.distributed_runtime = distributed_runtime

    def discover(self) -> tuple[DiscoveredComponent, ...]:
        components = [
            DiscoveredComponent(
                component_id="reference-orchestrator",
                category=ComponentCategory.ORCHESTRATOR,
                display_name="Reference Orchestrator",
                availability=ComponentAvailability.AVAILABLE,
                lifecycle=ComponentLifecycle.RECOMMENDED,
                recommended_modes=frozenset(
                    {SetupMode.AUTO, SetupMode.LOCAL, SetupMode.MULTI_NODE}
                ),
                source_ref="platform:orchestration/reference",
            ),
            DiscoveredComponent(
                component_id="reference-executor",
                category=ComponentCategory.EXECUTOR,
                display_name="Reference Executor",
                availability=ComponentAvailability.AVAILABLE,
                lifecycle=ComponentLifecycle.RECOMMENDED,
                recommended_modes=frozenset(
                    {SetupMode.AUTO, SetupMode.LOCAL, SetupMode.MULTI_NODE}
                ),
                source_ref="platform:execution/reference",
            ),
            DiscoveredComponent(
                component_id="native-capabilities",
                category=ComponentCategory.TOOLS_MCP,
                display_name="Native Capability Registry",
                availability=ComponentAvailability.AVAILABLE,
                lifecycle=ComponentLifecycle.RECOMMENDED,
                recommended_modes=frozenset(
                    {SetupMode.AUTO, SetupMode.LOCAL, SetupMode.MULTI_NODE}
                ),
                source_ref="platform:capabilities",
            ),
            DiscoveredComponent(
                component_id="local-files",
                category=ComponentCategory.STORAGE,
                display_name="Local File Storage",
                availability=ComponentAvailability.AVAILABLE,
                lifecycle=ComponentLifecycle.RECOMMENDED,
                recommended_modes=frozenset(
                    {SetupMode.AUTO, SetupMode.LOCAL, SetupMode.MULTI_NODE}
                ),
                source_ref="platform:data/local-file-provider",
            ),
            DiscoveredComponent(
                component_id="local-node",
                category=ComponentCategory.COMPUTE,
                display_name="Local Node",
                availability=ComponentAvailability.AVAILABLE,
                lifecycle=ComponentLifecycle.RECOMMENDED,
                recommended_modes=frozenset({SetupMode.AUTO, SetupMode.LOCAL}),
                source_ref="platform:deployment/single-node",
            ),
        ]
        components.extend(self._model_provider_components())
        distributed = self._distributed_component()
        if distributed is not None:
            components.append(distributed)
        return tuple(
            sorted(components, key=lambda item: (item.category.value, item.component_id))
        )

    def environment(self) -> CompatibilityEnvironment:
        capabilities = {
            "platform:orchestration",
            "platform:execution",
            "tools:canonical",
            "storage:local",
            "compute:local",
        }
        if self.distributed_runtime is not None:
            nodes = self.distributed_runtime.registry.list_nodes()
            workers = self.distributed_runtime.registry.list_workers()
            usable_nodes = tuple(
                node
                for node in nodes
                if node.status in {NodeStatus.ONLINE, NodeStatus.DEGRADED}
                and not node.draining
                and not node.maintenance
            )
            usable_node_ids = {node.node_id for node in usable_nodes}
            usable_workers = tuple(
                worker
                for worker in workers
                if worker.node_id in usable_node_ids
                and worker.status in {WorkerStatus.HEALTHY, WorkerStatus.DEGRADED}
                and not worker.draining
            )
            if usable_nodes and usable_workers:
                capabilities.add("compute:multi_node")
            for node in usable_nodes:
                if node.os_name:
                    capabilities.add(f"os:{node.os_name.casefold()}")
                if node.architecture:
                    capabilities.add(f"arch:{node.architecture.casefold()}")
                for runtime in node.supported_runtimes:
                    capabilities.add(f"runtime:{runtime.casefold()}")
                for accelerator in node.resources.accelerators:
                    if accelerator.memory_available_bytes <= 0:
                        continue
                    capabilities.add("gpu:any")
                    if accelerator.vendor:
                        capabilities.add(f"gpu:{accelerator.vendor.casefold()}")
        return CompatibilityEnvironment(capabilities=frozenset(capabilities))

    def _model_provider_components(self) -> tuple[DiscoveredComponent, ...]:
        components: list[DiscoveredComponent] = []
        for provider in self.models.list_providers():
            descriptor = provider.descriptor
            health = self.models.provider_health(descriptor.provider_id)
            available = descriptor.available and health in {
                HealthStatus.HEALTHY,
                HealthStatus.DEGRADED,
            }
            configured_models = self.models.list_models(
                provider_id=descriptor.provider_id,
                enabled=True,
            )
            model_locations = {model.location for model in configured_models}
            local_compatible = bool(
                model_locations & {ModelLocation.LOCAL, ModelLocation.SELF_HOSTED}
            )
            recommended_modes = (
                frozenset({SetupMode.AUTO, SetupMode.LOCAL, SetupMode.MULTI_NODE})
                if local_compatible
                else frozenset({SetupMode.ADVANCED})
            )
            capabilities = {
                capability.name for capability in descriptor.capabilities if capability.name.strip()
            }
            for capability in descriptor.capabilities:
                capabilities.update(
                    feature for feature in capability.features if feature.strip()
                )
            components.append(
                DiscoveredComponent(
                    component_id=descriptor.provider_id,
                    category=ComponentCategory.MODEL_PROVIDER,
                    display_name=descriptor.provider_id,
                    availability=(
                        ComponentAvailability.AVAILABLE
                        if available
                        else ComponentAvailability.UNAVAILABLE
                    ),
                    lifecycle=ComponentLifecycle.SUPPORTED,
                    version=descriptor.contract_version,
                    capabilities=frozenset(capabilities),
                    recommended_modes=recommended_modes,
                    source_ref=f"model-provider:{descriptor.provider_type}",
                    metadata={
                        "health": health.value,
                        "provider_type": descriptor.provider_type,
                        "supported_operations": list(descriptor.supported_operations),
                        "model_locations": sorted(location.value for location in model_locations),
                        "configured_model_count": len(configured_models),
                    },
                )
            )
        return tuple(components)

    def _distributed_component(self) -> DiscoveredComponent | None:
        if self.distributed_runtime is None:
            return None
        nodes = self.distributed_runtime.registry.list_nodes()
        workers = self.distributed_runtime.registry.list_workers()
        usable_node_ids = {
            node.node_id
            for node in nodes
            if node.status in {NodeStatus.ONLINE, NodeStatus.DEGRADED}
            and not node.draining
            and not node.maintenance
        }
        usable_workers = tuple(
            worker
            for worker in workers
            if worker.node_id in usable_node_ids
            and worker.status in {WorkerStatus.HEALTHY, WorkerStatus.DEGRADED}
            and not worker.draining
        )
        return DiscoveredComponent(
            component_id="distributed-workers",
            category=ComponentCategory.COMPUTE,
            display_name="Registered Worker Nodes",
            availability=(
                ComponentAvailability.AVAILABLE
                if usable_workers
                else ComponentAvailability.UNAVAILABLE
            ),
            lifecycle=ComponentLifecycle.SUPPORTED,
            capabilities=frozenset({"compute:multi_node"}) if usable_workers else frozenset(),
            recommended_modes=frozenset({SetupMode.MULTI_NODE}),
            source_ref="platform:distributed/runtime",
            metadata={
                "registered_node_count": len(nodes),
                "registered_worker_count": len(workers),
                "usable_worker_count": len(usable_workers),
            },
        )
