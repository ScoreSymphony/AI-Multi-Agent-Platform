from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.applications.control_plane import (
    APPLICATION_COLLECTIONS,
    APPLICATION_COMMANDS,
    register_application_control_plane,
)
from ai_multi_agent_platform.applications.models import (
    ApplicationDesiredState,
    ApplicationEndpointResolution,
    ApplicationHealthStatus,
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationLogEntry,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationService,
    ApplicationServiceRuntime,
)
from ai_multi_agent_platform.applications.repository import InMemoryApplicationRepository
from ai_multi_agent_platform.applications.runtime import (
    ApplicationRuntimeDescriptor,
    ApplicationRuntimeError,
)
from ai_multi_agent_platform.applications.serialization import application_manifest_to_document
from ai_multi_agent_platform.applications.service import (
    ApplicationLifecycleService,
    ApplicationRuntimeRegistry,
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


class _Runtime:
    def __init__(self) -> None:
        self._descriptor = ApplicationRuntimeDescriptor(
            runtime_id="reference.process",
            supported_service_runtimes=frozenset({ApplicationServiceRuntime.PROCESS}),
            capabilities=frozenset({"local"}),
        )
        self.fail_start = False

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
            configuration=request.resolved_configuration(),
            secret_bindings=request.secret_bindings,
            volume_bindings=request.volume_bindings,
        )

    async def start(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        del manifest
        if self.fail_start:
            raise ApplicationRuntimeError("start failed")
        return replace(
            instance,
            observed_state=ApplicationObservedState.RUNNING,
            health=ApplicationHealthStatus.HEALTHY,
        )

    async def stop(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        del manifest
        return replace(
            instance,
            observed_state=ApplicationObservedState.STOPPED,
            health=ApplicationHealthStatus.UNKNOWN,
        )

    async def restart(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        return await self.start(manifest, instance)

    async def remove(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        del manifest
        return replace(
            instance,
            observed_state=ApplicationObservedState.REMOVED,
            health=ApplicationHealthStatus.UNKNOWN,
        )

    async def status(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        del manifest
        return instance

    async def health(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationHealthStatus:
        del manifest
        return instance.health

    async def endpoints(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
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
        return ()

    async def reconcile(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        if instance.desired_state is ApplicationDesiredState.RUNNING:
            return await self.start(manifest, instance)
        if instance.desired_state is ApplicationDesiredState.REMOVED:
            return await self.remove(manifest, instance)
        return await self.stop(manifest, instance)

    async def recover(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        return await self.reconcile(manifest, instance)


def _manifest() -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Control Plane fixture",
        version="1.0.0",
        description="Application Control Plane fixture",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-m", "example"),
            ),
        ),
        runtime_requirements=("local",),
    )


def _stack(
    *,
    allowed: bool = True,
) -> tuple[
    ControlPlane,
    InMemoryApplicationRepository,
    _Runtime,
    FakeAuthorizationProvider,
]:
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    authorization = FakeAuthorizationProvider(allowed=allowed)
    control_plane = ControlPlane(
        kernel=kernel,
        events=events,
        authorization=authorization,
    )
    repository = InMemoryApplicationRepository()
    runtime = _Runtime()
    lifecycle = ApplicationLifecycleService(
        repository,
        ApplicationRuntimeRegistry((runtime,)),
    )
    register_application_control_plane(control_plane, lifecycle, repository)
    return control_plane, repository, runtime, authorization


def _context(*, key: str = "command-key") -> RequestContext:
    return RequestContext(
        request_id="request-1",
        correlation_id="correlation-1",
        actor=ActorContext(
            principal_ref="user-test",
            owner_type="user",
            owner_id="user-test",
            actor_type="human",
        ),
        idempotency_key=key,
    )


async def _install(control_plane: ControlPlane, manifest: ApplicationManifest) -> dict[str, object]:
    result = await control_plane.execute_command(
        _context(),
        "application.install",
        manifest.application_id,
        {
            "manifest": application_manifest_to_document(manifest),
            "runtime_id": "reference.process",
        },
    )
    return result


async def test_application_module_registers_canonical_resources_and_commands() -> None:
    control_plane, _, _, _ = _stack()

    assert set(APPLICATION_COLLECTIONS) <= set(control_plane.registered_collections)
    assert set(APPLICATION_COMMANDS) <= set(control_plane.registered_commands)
    assert control_plane.resource_owner("applications") == "applications"
    assert control_plane.command_owner("application.start") == "applications"


async def test_install_is_payload_bound_and_resources_are_readable() -> None:
    control_plane, _, _, authorization = _stack()
    manifest = _manifest()

    installed = await _install(control_plane, manifest)

    assert installed["application_id"] == manifest.application_id
    assert installed["desired_state"] == "stopped"
    assert authorization.calls[-1].action == "application.install"
    assert authorization.calls[-1].request_payload_digest is not None

    applications = await control_plane.list_extension_resources(
        _context(key="read-key"),
        "applications",
        PageQuery(),
    )
    resources = applications["items"]
    assert isinstance(resources, list)
    assert resources[0]["application_id"] == manifest.application_id

    instance_id = installed["id"]
    assert isinstance(instance_id, str)
    instance = await control_plane.get_extension_resource(
        _context(key="read-instance"),
        "application-instances",
        instance_id,
    )
    assert instance["id"] == instance_id


async def test_lifecycle_commands_converge_through_registered_boundary() -> None:
    control_plane, repository, _, _ = _stack()
    manifest = _manifest()
    installed = await _install(control_plane, manifest)
    instance_id = installed["id"]
    assert isinstance(instance_id, str)

    running = await control_plane.execute_command(
        _context(key="start-key"),
        "application.start",
        instance_id,
        {},
    )
    assert running["desired_state"] == "running"
    assert running["observed_state"] == "running"
    assert repository.get_instance(instance_id).health is ApplicationHealthStatus.HEALTHY

    stopped = await control_plane.execute_command(
        _context(key="stop-key"),
        "application.stop",
        instance_id,
        {},
    )
    assert stopped["desired_state"] == "stopped"
    assert stopped["observed_state"] == "stopped"


async def test_runtime_failures_are_canonicalized_at_control_plane_boundary() -> None:
    control_plane, repository, runtime, _ = _stack()
    manifest = _manifest()
    installed = await _install(control_plane, manifest)
    instance_id = installed["id"]
    assert isinstance(instance_id, str)
    runtime.fail_start = True

    with pytest.raises(ContractError) as raised:
        await control_plane.execute_command(
            _context(key="failing-start"),
            "application.start",
            instance_id,
            {},
        )

    assert raised.value.code is ErrorCode.BACKEND_ERROR
    failed = repository.get_instance(instance_id)
    assert failed.desired_state is ApplicationDesiredState.RUNNING
    assert failed.observed_state is ApplicationObservedState.FAILED


async def test_authorization_denial_happens_before_application_mutation() -> None:
    control_plane, repository, _, authorization = _stack(allowed=False)
    manifest = _manifest()

    with pytest.raises(ContractError) as raised:
        await _install(control_plane, manifest)

    assert raised.value.code is ErrorCode.FORBIDDEN
    assert repository.list_applications() == ()
    assert authorization.calls[-1].request_payload_digest is not None


async def test_lifecycle_commands_reject_unexpected_payload() -> None:
    control_plane, _, _, _ = _stack()
    manifest = _manifest()
    installed = await _install(control_plane, manifest)
    instance_id = installed["id"]
    assert isinstance(instance_id, str)

    with pytest.raises(ContractError) as raised:
        await control_plane.execute_command(
            _context(key="bad-payload"),
            "application.start",
            instance_id,
            {"unexpected": True},
        )

    assert raised.value.code is ErrorCode.INVALID_REQUEST
