from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.applications import (
    ApplicationDesiredState,
    ApplicationEndpointResolution,
    ApplicationHealthStatus,
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationLifecycleService,
    ApplicationLogEntry,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationRuntimeDescriptor,
    ApplicationRuntimeRegistry,
    ApplicationRuntimeUnavailableError,
    ApplicationService,
    ApplicationServiceRuntime,
    InMemoryApplicationRepository,
    register_application_control_plane,
    register_application_log_control_plane,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)

pytestmark = pytest.mark.asyncio


class _LogRuntime:
    def __init__(self) -> None:
        self._descriptor = ApplicationRuntimeDescriptor(
            runtime_id="reference.process",
            supported_service_runtimes=frozenset({ApplicationServiceRuntime.PROCESS}),
            capabilities=frozenset({"local"}),
        )
        self.logs_unavailable = False

    @property
    def descriptor(self) -> ApplicationRuntimeDescriptor:
        return self._descriptor

    async def prepare(self, request: ApplicationInstallRequest) -> ApplicationInstance:
        return ApplicationInstance(
            application_id=request.manifest.application_id,
            application_version=request.manifest.version,
            runtime_id=self.descriptor.runtime_id,
            desired_state=ApplicationDesiredState.STOPPED,
            observed_state=ApplicationObservedState.STOPPED,
            health=ApplicationHealthStatus.UNKNOWN,
        )

    async def start(
        self, manifest: ApplicationManifest, instance: ApplicationInstance
    ) -> ApplicationInstance:
        del manifest
        return replace(
            instance,
            observed_state=ApplicationObservedState.RUNNING,
            health=ApplicationHealthStatus.HEALTHY,
        )

    async def stop(
        self, manifest: ApplicationManifest, instance: ApplicationInstance
    ) -> ApplicationInstance:
        del manifest
        return replace(
            instance,
            observed_state=ApplicationObservedState.STOPPED,
            health=ApplicationHealthStatus.UNKNOWN,
        )

    async def restart(
        self, manifest: ApplicationManifest, instance: ApplicationInstance
    ) -> ApplicationInstance:
        return await self.start(manifest, instance)

    async def remove(
        self, manifest: ApplicationManifest, instance: ApplicationInstance
    ) -> ApplicationInstance:
        del manifest
        return replace(instance, observed_state=ApplicationObservedState.REMOVED)

    async def status(
        self, manifest: ApplicationManifest, instance: ApplicationInstance
    ) -> ApplicationInstance:
        del manifest
        return instance

    async def health(
        self, manifest: ApplicationManifest, instance: ApplicationInstance
    ) -> ApplicationHealthStatus:
        del manifest
        return instance.health

    async def endpoints(
        self, manifest: ApplicationManifest, instance: ApplicationInstance
    ) -> tuple[ApplicationEndpointResolution, ...]:
        del manifest, instance
        return ()

    async def logs(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        *,
        service_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ApplicationLogEntry, ...]:
        del manifest, instance, service_id, limit
        if self.logs_unavailable:
            raise ApplicationRuntimeUnavailableError("diagnostics backend unavailable")
        return (
            ApplicationLogEntry(message="ready", service_id="app"),
            ApplicationLogEntry(message="warning", service_id="app", level="warning"),
        )

    async def reconcile(
        self, manifest: ApplicationManifest, instance: ApplicationInstance
    ) -> ApplicationInstance:
        del manifest
        return instance

    async def recover(
        self, manifest: ApplicationManifest, instance: ApplicationInstance
    ) -> ApplicationInstance:
        del manifest
        return instance


def _manifest() -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Diagnostics fixture",
        version="1.0.0",
        description="Canonical application diagnostics fixture",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-m", "example"),
            ),
        ),
        runtime_requirements=("local",),
    )


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-logs",
        correlation_id="correlation-logs",
        actor=ActorContext(
            principal_ref="user-test",
            owner_type="user",
            owner_id="user-test",
            actor_type="human",
        ),
        idempotency_key="logs-key",
    )


async def _stack() -> tuple[ControlPlane, ApplicationLifecycleService, _LogRuntime]:
    events = InMemoryKernelRepository()
    control_plane = ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        ),
        events=events,
        authorization=FakeAuthorizationProvider(allowed=True),
    )
    repository = InMemoryApplicationRepository()
    runtime = _LogRuntime()
    lifecycle = ApplicationLifecycleService(
        repository,
        ApplicationRuntimeRegistry((runtime,)),
    )
    register_application_control_plane(control_plane, lifecycle, repository)
    register_application_log_control_plane(control_plane, lifecycle, repository)
    return control_plane, lifecycle, runtime


async def test_application_log_stream_is_metadata_only_until_direct_read() -> None:
    control_plane, lifecycle, _ = await _stack()
    installed = await lifecycle.install(
        ApplicationInstallRequest(manifest=_manifest()),
        runtime_id="reference.process",
    )

    page = await control_plane.list_extension_resources(
        _context(),
        "application-logs",
        PageQuery(),
    )
    items = page["items"]
    assert isinstance(items, list)
    assert items == [
        {
            "id": installed.instance_id,
            "type": "application-log-stream",
            "instance_id": installed.instance_id,
            "application_id": installed.application_id,
            "application_version": "1.0.0",
            "runtime_id": "reference.process",
            "service_ids": [],
        }
    ]

    stream = await control_plane.get_extension_resource(
        _context(),
        "application-logs",
        installed.instance_id,
    )
    assert stream["instance_id"] == installed.instance_id
    entries = stream["entries"]
    assert isinstance(entries, list)
    assert [entry["message"] for entry in entries] == ["ready", "warning"]
    assert [entry["level"] for entry in entries] == ["info", "warning"]


async def test_application_log_runtime_unavailability_is_canonicalized() -> None:
    control_plane, lifecycle, runtime = await _stack()
    installed = await lifecycle.install(
        ApplicationInstallRequest(manifest=_manifest()),
        runtime_id="reference.process",
    )
    runtime.logs_unavailable = True

    with pytest.raises(ContractError) as raised:
        await control_plane.get_extension_resource(
            _context(),
            "application-logs",
            installed.instance_id,
        )

    assert raised.value.code is ErrorCode.UNAVAILABLE
    assert raised.value.retryable is True
