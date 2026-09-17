"""Canonical northbound diagnostics for managed Application instances."""

from __future__ import annotations

from collections.abc import Awaitable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.extensions import (
    ControlPlane,
    ControlPlaneModule,
    ResourceService,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.control_plane.module_registry import install_control_plane_modules

from .models import ApplicationInstance, ApplicationLogEntry
from .repository import ApplicationRepository
from .runtime import ApplicationRuntimeError, ApplicationRuntimeUnavailableError
from .service import ApplicationLifecycleService

APPLICATION_LOG_COLLECTION = "application-logs"
APPLICATION_LOG_MODULE = "applications.logs"


class ApplicationLogResourceService(ResourceService):
    """Expose one bounded canonical log stream resource per Application Instance."""

    def __init__(
        self,
        lifecycle: ApplicationLifecycleService,
        repository: ApplicationRepository,
    ) -> None:
        self._lifecycle = lifecycle
        self._repository = repository

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        # Listing streams is metadata-only. Fetching runtime logs for every Application
        # would turn a cheap inventory operation into an implicit fan-out across backends.
        return tuple(_log_stream_resource(item) for item in self._repository.list_instances())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        instance = self._repository.get_instance(resource_id)
        entries = await _logs_boundary(self._lifecycle.logs(resource_id))
        return _log_stream_resource(instance, entries=entries)


def application_log_control_plane_module(
    lifecycle: ApplicationLifecycleService,
    repository: ApplicationRepository,
) -> ControlPlaneModule:
    """Build the diagnostics module as a child of canonical Application ownership."""

    return ControlPlaneModule(
        name=APPLICATION_LOG_MODULE,
        requires=frozenset({"applications"}),
        resource_services={
            APPLICATION_LOG_COLLECTION: ApplicationLogResourceService(lifecycle, repository)
        },
    )


def register_application_log_control_plane(
    control_plane: ControlPlane,
    lifecycle: ApplicationLifecycleService,
    repository: ApplicationRepository,
) -> None:
    """Register bounded Application diagnostics on the canonical Control Plane."""

    install_control_plane_modules(
        control_plane,
        (application_log_control_plane_module(lifecycle, repository),),
    )


def _log_stream_resource(
    instance: ApplicationInstance,
    *,
    entries: tuple[ApplicationLogEntry, ...] | None = None,
) -> dict[str, JsonValue]:
    resource: dict[str, JsonValue] = {
        "id": instance.instance_id,
        "type": "application-log-stream",
        "instance_id": instance.instance_id,
        "application_id": instance.application_id,
        "application_version": instance.application_version,
        "runtime_id": instance.runtime_id,
        "service_ids": [state.service_id for state in instance.service_states],
    }
    if entries is not None:
        resource["entries"] = [
            {
                "timestamp": entry.timestamp.isoformat(),
                "service_id": entry.service_id,
                "level": entry.level,
                "message": entry.message,
            }
            for entry in entries
        ]
    return resource


async def _logs_boundary(
    operation: Awaitable[tuple[ApplicationLogEntry, ...]],
) -> tuple[ApplicationLogEntry, ...]:
    try:
        return await operation
    except ApplicationRuntimeUnavailableError as exc:
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            str(exc),
            retryable=True,
        ) from exc
    except ApplicationRuntimeError as exc:
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            str(exc),
        ) from exc


__all__ = [
    "APPLICATION_LOG_COLLECTION",
    "APPLICATION_LOG_MODULE",
    "ApplicationLogResourceService",
    "application_log_control_plane_module",
    "register_application_log_control_plane",
]
