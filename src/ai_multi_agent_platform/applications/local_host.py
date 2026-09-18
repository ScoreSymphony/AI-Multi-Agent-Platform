"""Backend-private local host suitability for PROCESS Application execution."""

from __future__ import annotations

import os
import platform
import shutil
from collections.abc import Iterable
from dataclasses import dataclass

from .models import ApplicationResourceRequirements


@dataclass(frozen=True, slots=True)
class LocalApplicationHostProfile:
    """Conservative suitability facts for the current local execution host."""

    architecture: str
    operating_system: str
    cpu_cores: float | None = None
    memory_bytes: int | None = None
    disk_available_bytes: int | None = None
    gpu_count: int = 0
    capabilities: frozenset[str] = frozenset()
    labels: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.architecture.strip():
            raise ValueError("host architecture must not be blank")
        if not self.operating_system.strip():
            raise ValueError("host operating_system must not be blank")
        if self.cpu_cores is not None and self.cpu_cores <= 0:
            raise ValueError("host cpu_cores must be > 0 when known")
        if self.memory_bytes is not None and self.memory_bytes < 0:
            raise ValueError("host memory_bytes must be >= 0 when known")
        if self.disk_available_bytes is not None and self.disk_available_bytes < 0:
            raise ValueError("host disk_available_bytes must be >= 0 when known")
        if self.gpu_count < 0:
            raise ValueError("host gpu_count must be >= 0")
        if any(not value.strip() for value in self.capabilities):
            raise ValueError("host capabilities must not contain blank values")
        if any(not value.strip() for value in self.labels):
            raise ValueError("host labels must not contain blank values")
        object.__setattr__(self, "architecture", _canonical_architecture(self.architecture))
        object.__setattr__(
            self,
            "operating_system",
            _canonical_operating_system(self.operating_system),
        )


def detect_local_application_host_profile(
    *,
    disk_path: os.PathLike[str] | str | None = None,
) -> LocalApplicationHostProfile:
    """Detect local facts with stdlib-only probes and conservative unknowns.

    GPU inventory, custom labels and specialized capabilities are intentionally not
    guessed. Deployments with an authoritative hardware inventory may inject an
    explicit profile instead.
    """

    cpu_count = os.cpu_count()
    return LocalApplicationHostProfile(
        architecture=platform.machine() or "unknown",
        operating_system=platform.system() or "unknown",
        cpu_cores=None if cpu_count is None else float(cpu_count),
        memory_bytes=_physical_memory_bytes(),
        disk_available_bytes=_available_disk_bytes(disk_path),
    )


def local_host_resource_rejections(
    requirements: ApplicationResourceRequirements,
    *,
    host: LocalApplicationHostProfile,
    runtime_capabilities: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return unsatisfied requirement dimensions for one local execution host."""

    rejected: list[str] = []
    if requirements.cpu_cores is not None and (
        host.cpu_cores is None or host.cpu_cores < requirements.cpu_cores
    ):
        rejected.append("cpu")
    if requirements.memory_bytes is not None and (
        host.memory_bytes is None or host.memory_bytes < requirements.memory_bytes
    ):
        rejected.append("memory")
    if requirements.disk_bytes is not None and (
        host.disk_available_bytes is None
        or host.disk_available_bytes < requirements.disk_bytes
    ):
        rejected.append("disk")
    if host.gpu_count < requirements.gpu_count:
        rejected.append("gpu")

    accepted_architectures = {
        _canonical_architecture(value) for value in requirements.architectures
    }
    if accepted_architectures and host.architecture not in accepted_architectures:
        rejected.append("architecture")

    accepted_systems = {
        _canonical_operating_system(value) for value in requirements.operating_systems
    }
    if accepted_systems and host.operating_system not in accepted_systems:
        rejected.append("operating_system")

    available_capabilities = set(host.capabilities) | set(runtime_capabilities)
    if set(requirements.required_capabilities) - available_capabilities:
        rejected.append("capabilities")
    if set(requirements.required_labels) - set(host.labels):
        rejected.append("labels")
    return tuple(rejected)


def _canonical_architecture(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "x86-64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    return aliases.get(normalized, normalized)


def _canonical_operating_system(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "darwin": "macos",
        "mac": "macos",
        "macos": "macos",
        "windows": "windows",
        "win32": "windows",
        "linux": "linux",
    }
    return aliases.get(normalized, normalized)


def _physical_memory_bytes() -> int | None:
    sysconf = getattr(os, "sysconf", None)
    names = getattr(os, "sysconf_names", {})
    if not callable(sysconf) or "SC_PAGE_SIZE" not in names or "SC_PHYS_PAGES" not in names:
        return None
    try:
        page_size = int(sysconf("SC_PAGE_SIZE"))
        page_count = int(sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError):
        return None
    if page_size <= 0 or page_count <= 0:
        return None
    return page_size * page_count


def _available_disk_bytes(path: os.PathLike[str] | str | None) -> int | None:
    target = os.getcwd() if path is None else os.fspath(path)
    try:
        return shutil.disk_usage(target).free
    except OSError:
        return None


__all__ = [
    "LocalApplicationHostProfile",
    "detect_local_application_host_profile",
    "local_host_resource_rejections",
]
