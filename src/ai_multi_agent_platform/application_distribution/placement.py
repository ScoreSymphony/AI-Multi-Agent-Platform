"""Build-target placement helpers over the canonical distributed inventory."""

from __future__ import annotations

from ai_multi_agent_platform.distributed import DistributedRegistry, NodeStatus, WorkerStatus

from .models import BuildSpecification, BuildTarget


class DistributedBuildTargetMatcher:
    """Match OS/architecture/toolchain requirements to healthy canonical Workers."""

    def __init__(self, registry: DistributedRegistry) -> None:
        self.registry = registry

    async def supports(
        self,
        specification: BuildSpecification,
        target: BuildTarget,
    ) -> bool:
        required_capabilities = set(specification.required_capabilities)
        required_capabilities.update(target.required_capabilities)
        for worker in self.registry.list_workers():
            if worker.status is not WorkerStatus.HEALTHY or worker.draining:
                continue
            node = self.registry.get_node(worker.node_id)
            if node.status is not NodeStatus.ONLINE or node.draining or node.maintenance:
                continue
            if node.os_name != target.os_name or node.architecture != target.architecture:
                continue
            if required_capabilities - set(worker.capability_refs):
                continue
            return True
        return False


__all__ = ["DistributedBuildTargetMatcher"]
