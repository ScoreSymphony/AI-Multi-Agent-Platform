"""Platform-owned Application Runtime contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .models import (
    ApplicationEndpointResolution,
    ApplicationHealthStatus,
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationLogEntry,
    ApplicationManifest,
    ApplicationServiceRuntime,
)


class ApplicationRuntimeError(RuntimeError):
    """Base error for provider-neutral Application Runtime failures."""


class ApplicationInstanceNotFoundError(ApplicationRuntimeError):
    """The canonical Application Instance is unknown to this runtime."""


class ApplicationRuntimeUnavailableError(ApplicationRuntimeError):
    """The runtime backend cannot currently satisfy an operation."""


class ApplicationPreparationError(ApplicationRuntimeError):
    """The runtime could not prepare the declared Application."""


@dataclass(frozen=True, slots=True)
class ApplicationRuntimeDescriptor:
    runtime_id: str
    supported_service_runtimes: frozenset[ApplicationServiceRuntime]
    capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.runtime_id.strip():
            raise ValueError("runtime_id must not be blank")
        if not self.supported_service_runtimes:
            raise ValueError("runtime must support at least one service runtime")


@runtime_checkable
class ApplicationRuntime(Protocol):
    """Provider-neutral lifecycle boundary for managed external applications."""

    @property
    def descriptor(self) -> ApplicationRuntimeDescriptor: ...

    def prepare(self, request: ApplicationInstallRequest) -> ApplicationInstance:
        """Validate/materialize an instance without implicitly starting it."""

    def start(self, instance_id: str) -> ApplicationInstance:
        """Set desired state to running and attempt to reach it."""

    def stop(self, instance_id: str) -> ApplicationInstance:
        """Set desired state to stopped and attempt to reach it."""

    def restart(self, instance_id: str) -> ApplicationInstance:
        """Restart one prepared instance without changing its canonical identity."""

    def remove(self, instance_id: str) -> ApplicationInstance:
        """Set desired state to removed and release runtime-owned resources."""

    def status(self, instance_id: str) -> ApplicationInstance:
        """Return canonical desired/observed state without provider-private identifiers."""

    def health(self, instance_id: str) -> ApplicationHealthStatus:
        """Return aggregate application health."""

    def endpoints(self, instance_id: str) -> tuple[ApplicationEndpointResolution, ...]:
        """Return runtime-resolved logical endpoints."""

    def logs(
        self,
        instance_id: str,
        *,
        service_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ApplicationLogEntry, ...]:
        """Return bounded canonical logs for an instance or service."""

    def reconcile(self, instance_id: str) -> ApplicationInstance:
        """Converge observed state toward the persisted desired state."""

    def recover(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Re-adopt persisted canonical state after runtime/platform restart."""
