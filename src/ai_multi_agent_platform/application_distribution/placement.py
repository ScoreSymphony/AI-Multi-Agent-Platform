"""Build-target placement helpers over local and distributed execution inventories."""

from __future__ import annotations

import platform
import shutil

from ai_multi_agent_platform.distributed import DistributedRegistry, NodeStatus, WorkerStatus

from .models import BuildSpecification, BuildTarget


class LocalBuildTargetMatcher:
    """Admit only targets the current host can truthfully execute."""

    def __init__(self, capabilities: tuple[str, ...] = ()) -> None:
        self._capabilities = frozenset(capabilities)

    async def supports(
        self,
        specification: BuildSpecification,
        target: BuildTarget,
    ) -> bool:
        os_name = _local_os()
        architecture = _local_architecture()
        if _normalize_os(target.os_name) != os_name:
            return False
        if _normalize_architecture(target.architecture) != architecture:
            return False
        command = specification.command
        if command and shutil.which(command[0]) is None:
            return False
        available = set(self._capabilities)
        available.update({f"os:{os_name}", f"arch:{architecture}"})
        if command and _python_command(command[0]):
            available.add("python")
        required = set(specification.required_capabilities)
        required.update(target.required_capabilities)
        return not (required - available)


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
            if _normalize_os(node.os_name) != _normalize_os(target.os_name):
                continue
            if _normalize_architecture(node.architecture) != _normalize_architecture(
                target.architecture
            ):
                continue
            if required_capabilities - set(worker.capability_refs):
                continue
            return True
        return False


def _local_os() -> str:
    return _normalize_os(platform.system())


def _local_architecture() -> str:
    return _normalize_architecture(platform.machine())


def _normalize_os(value: str) -> str:
    normalized = value.strip().lower()
    return {
        "darwin": "macos",
        "mac": "macos",
        "macos": "macos",
        "win32": "windows",
        "windows": "windows",
        "linux": "linux",
    }.get(normalized, normalized)


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().lower()
    return {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86-64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(normalized, normalized)


def _python_command(value: str) -> bool:
    name = value.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in {"python", "python3", "python.exe"} or name.startswith("python3.")


__all__ = ["DistributedBuildTargetMatcher", "LocalBuildTargetMatcher"]
