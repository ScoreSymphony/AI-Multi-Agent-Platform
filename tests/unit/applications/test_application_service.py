from __future__ import annotations

from dataclasses import replace

import pytest

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
from ai_multi_agent_platform.applications.service import (
    ApplicationLifecycleService,
    ApplicationRuntimeRegistry,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id

pytestmark = pytest.mark.asyncio


class RecordingRuntime:
    def __init__(
        self,
        *,
        runtime_id: str = "reference.process",
        supported: frozenset[ApplicationServiceRuntime] = frozenset(
            {ApplicationServiceRuntime.PROCESS}
        ),
    ) -> None:
        self._descriptor = ApplicationRuntimeDescriptor(
            runtime_id=runtime_id,
            supported_service_runtimes=supported,
            capabilities=frozenset({"local"}),
        )
        self.start_intent: ApplicationDesiredState | None = None
        self.recover_intent: ApplicationDesiredState | None = None
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
        self.start_intent = instance.desired_state
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
        del manifest
        return replace(
            instance,
            observed_state=ApplicationObservedState.RUNNING,
            health=ApplicationHealthStatus.HEALTHY,
        )

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
        return await self.recover(manifest, instance)

    async def recover(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        del manifest
        self.recover_intent = instance.desired_state
        if instance.desired_state is ApplicationDesiredState.RUNNING:
            return replace(
                instance,
                observed_state=ApplicationObservedState.RUNNING,
                health=ApplicationHealthStatus.HEALTHY,
            )
        if instance.desired_state is ApplicationDesiredState.REMOVED:
            return replace(
                instance,
                observed_state=ApplicationObservedState.REMOVED,
                health=ApplicationHealthStatus.UNKNOWN,
            )
        return replace(
            instance,
            observed_state=ApplicationObservedState.STOPPED,
            health=ApplicationHealthStatus.UNKNOWN,
        )


def _manifest(
    runtime: ApplicationServiceRuntime = ApplicationServiceRuntime.PROCESS,
) -> ApplicationManifest:
    if runtime is ApplicationServiceRuntime.PROCESS:
        service = ApplicationService(
            service_id="app",
            runtime=runtime,
            process=("python", "-m", "example"),
        )
    else:
        service = ApplicationService(
            service_id="app",
            runtime=runtime,
            image="example/app:1.0",
        )
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Lifecycle example",
        version="1.0.0",
        description="Application lifecycle fixture",
        services=(service,),
        runtime_requirements=("local",),
    )


async def _service(
    manifest: ApplicationManifest,
    runtime: RecordingRuntime,
) -> tuple[ApplicationLifecycleService, InMemoryApplicationRepository, str]:
    repository = InMemoryApplicationRepository()
    lifecycle = ApplicationLifecycleService(
        repository,
        ApplicationRuntimeRegistry((runtime,)),
    )
    instance = await lifecycle.install(
        ApplicationInstallRequest(manifest=manifest),
        runtime_id=runtime.descriptor.runtime_id,
    )
    return lifecycle, repository, instance.instance_id


async def test_start_persists_running_intent_before_runtime_convergence() -> None:
    runtime = RecordingRuntime()
    lifecycle, repository, instance_id = await _service(_manifest(), runtime)

    running = await lifecycle.start(instance_id)

    assert runtime.start_intent is ApplicationDesiredState.RUNNING
    assert running.desired_state is ApplicationDesiredState.RUNNING
    assert running.observed_state is ApplicationObservedState.RUNNING
    assert running.health is ApplicationHealthStatus.HEALTHY
    assert repository.get_instance(instance_id) == running
    assert running.revision >= 3


async def test_start_failure_keeps_running_intent_and_persists_failed_observation() -> None:
    runtime = RecordingRuntime()
    lifecycle, repository, instance_id = await _service(_manifest(), runtime)
    runtime.fail_start = True

    with pytest.raises(ApplicationRuntimeError, match="start failed"):
        await lifecycle.start(instance_id)

    failed = repository.get_instance(instance_id)
    assert failed.desired_state is ApplicationDesiredState.RUNNING
    assert failed.observed_state is ApplicationObservedState.FAILED
    assert failed.health is ApplicationHealthStatus.UNHEALTHY


async def test_recovery_converges_from_persisted_desired_state() -> None:
    runtime = RecordingRuntime()
    lifecycle, repository, instance_id = await _service(_manifest(), runtime)
    stopped = repository.get_instance(instance_id)
    repository.save_instance(
        replace(
            stopped,
            desired_state=ApplicationDesiredState.RUNNING,
            revision=stopped.revision + 1,
        )
    )

    report = await lifecycle.recover_all()

    assert report.failures == ()
    assert runtime.recover_intent is ApplicationDesiredState.RUNNING
    recovered = repository.get_instance(instance_id)
    assert recovered.observed_state is ApplicationObservedState.RUNNING
    assert recovered.health is ApplicationHealthStatus.HEALTHY


async def test_install_rejects_unsupported_service_runtime() -> None:
    runtime = RecordingRuntime()
    lifecycle = ApplicationLifecycleService(
        InMemoryApplicationRepository(),
        ApplicationRuntimeRegistry((runtime,)),
    )

    with pytest.raises(ContractError) as exc_info:
        await lifecycle.install(
            ApplicationInstallRequest(manifest=_manifest(ApplicationServiceRuntime.OCI_IMAGE)),
            runtime_id=runtime.descriptor.runtime_id,
        )

    assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
