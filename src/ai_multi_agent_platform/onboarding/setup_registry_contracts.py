"""Provider-neutral Registry projection used by browser-first setup.

The onboarding package coordinates setup but does not own Registry discovery or mutation.  These
small immutable projections and the port below keep that dependency pointing outward so concrete
Distribution/Marketplace implementations can be adapted at the deployment boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.control_plane.models import RequestContext


@dataclass(frozen=True, slots=True)
class SetupRegistryDependency:
    """Required/optional dependency range projected from the Registry owner domain."""

    item_id: str
    minimum_version: str | None = None
    maximum_version: str | None = None
    optional: bool = False

    def contains(self, version: str) -> bool:
        candidate = setup_version_key(version)
        if self.minimum_version is not None and candidate < setup_version_key(self.minimum_version):
            return False
        if self.maximum_version is not None and candidate > setup_version_key(self.maximum_version):
            return False
        return True


@dataclass(frozen=True, slots=True)
class SetupRegistryTechnicalMetadata:
    """Only the product-facing technical metadata setup needs for presentation."""

    deployment_modes: tuple[str, ...] = ()
    network_status: str | None = None
    lifecycle_status: str | None = None


@dataclass(frozen=True, slots=True)
class SetupRegistryItem:
    """Read-only Registry item projection safe for the onboarding package."""

    item_id: str
    version: str
    name: str
    description: str
    categories: tuple[str, ...]
    route: str
    deprecated: bool
    yanked: bool
    dependencies: tuple[SetupRegistryDependency, ...]
    license: str
    source_repository: str
    technical: SetupRegistryTechnicalMetadata | None = None


class SetupRegistryPort(Protocol):
    """Read/preview/activate seam implemented by the concrete Registry adapter."""

    @property
    def enabled(self) -> bool: ...

    @property
    def mutation_enabled(self) -> bool: ...

    def search(self) -> tuple[SetupRegistryItem, ...]: ...

    def get(self, item_id: str, version: str) -> SetupRegistryItem: ...

    def installed_version(self, item_id: str) -> str | None: ...

    async def preview(
        self,
        context: RequestContext,
        item_id: str,
        version: str,
    ) -> None: ...

    async def activate(
        self,
        context: RequestContext,
        item_id: str,
        version: str,
    ) -> None: ...


def setup_version_key(value: str) -> tuple[int, int, int]:
    """Compare the Registry's canonical one-to-three-part numeric dotted versions."""

    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError("version must be a one-to-three-part numeric dotted version")
    numeric = [int(part) for part in parts]
    numeric.extend([0] * (3 - len(numeric)))
    return numeric[0], numeric[1], numeric[2]


__all__ = [
    "SetupRegistryDependency",
    "SetupRegistryItem",
    "SetupRegistryPort",
    "SetupRegistryTechnicalMetadata",
    "setup_version_key",
]
