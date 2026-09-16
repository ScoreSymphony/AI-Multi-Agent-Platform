"""Platform-owned asynchronous Application Runtime contract."""

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
    """Provider-neutral async lifecycle boundary for managed external applications.

    Runtime operations may perform process, network, secret or workspace I/O and are
    therefore asynchronous. Canonical state is supplied to lifecycle operations instead
    of requiring the backend to be the durable source of truth. A backend may keep
    private runtime handles, but those handles never become canonical Application IDs.
    """

    @property
    def descriptor(self) -> ApplicationRuntimeDescriptor: ...

    async def prepare(self, request: ApplicationInstallRequest) -> ApplicationInstance:
        """Validate/materialize an instance without implicitly starting it."""

    async def start(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Attempt to converge persisted running intent to a running instance."""

    async def stop(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Attempt to converge persisted stopped intent to a stopped instance."""

    async def restart(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Restart one prepared instance without changing canonical identity."""

    async def remove(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Release runtime-owned resources for a persisted removed intent."""

    async def status(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Return canonical observed state without provider-private identifiers."""

    async def health(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationHealthStatus:
        """Return aggregate application health."""

    async def endpoints(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> tuple[ApplicationEndpointResolution, ...]:
        """Return runtime-resolved logical endpoints."""

    async def logs(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        *,
        service_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ApplicationLogEntry, ...]:
        """Return bounded canonical logs for an instance or service."""

    async def reconcile(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Converge observed state toward the persisted desired state."""

    async def recover(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        """Re-adopt or safely fail persisted canonical state after runtime restart."""
