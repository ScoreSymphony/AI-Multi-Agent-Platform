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
    """Provider-neutral lifecycle boundary for managed external applications.

    Canonical state is supplied to lifecycle operations instead of requiring the backend
    to be the durable source of truth. A backend may keep private runtime handles, but
    restart recovery can always be driven from the persisted manifest and instance.
    """

    @property
    def descriptor(self) -> ApplicationRuntimeDescriptor: ...

    def prepare(self, request: ApplicationInstallRequest) -> ApplicationInstance:
        """Validate/materialize an instance without implicitly starting it."""

    def start(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Attempt to converge the persisted running intent to a running instance."""

    def stop(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Attempt to converge the persisted stopped intent to a stopped instance."""

    def restart(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Restart one prepared instance without changing its canonical identity."""

    def remove(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Release runtime-owned resources for a persisted removed intent."""

    def status(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Return canonical observed state without provider-private identifiers."""

    def health(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationHealthStatus:
        """Return aggregate application health."""

    def endpoints(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> tuple[ApplicationEndpointResolution, ...]:
        """Return runtime-resolved logical endpoints."""

    def logs(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        *,
        service_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ApplicationLogEntry, ...]:
        """Return bounded canonical logs for an instance or service."""

    def reconcile(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Converge observed state toward the persisted desired state."""

    def recover(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Re-adopt persisted canonical state after runtime/platform restart."""
