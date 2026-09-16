from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.applications.definition import Application
from ai_multi_agent_platform.applications.models import (
    ApplicationConfigValueType,
    ApplicationConfigurationField,
    ApplicationDesiredState,
    ApplicationEndpoint,
    ApplicationEndpointExposure,
    ApplicationEndpointResolution,
    ApplicationHealthCheck,
    ApplicationHealthCheckKind,
    ApplicationHealthStatus,
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationLogEntry,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationResourceAssociation,
    ApplicationResourceRequirements,
    ApplicationSecretField,
    ApplicationService,
    ApplicationServiceRuntime,
    ApplicationServiceState,
    ApplicationUi,
    ApplicationUiOpenMode,
    ApplicationVolume,
    ApplicationVolumeBinding,
    ApplicationVolumeKind,
    ApplicationVolumeMount,
)
from ai_multi_agent_platform.applications.runtime import (
    ApplicationRuntimeDescriptor,
    ApplicationRuntimeError,
)
from ai_multi_agent_platform.applications.service import (
    ApplicationLifecycleService,
    ApplicationRuntimeRegistry,
)
from ai_multi_agent_platform.applications.sqlite_repository import SqliteApplicationRepository
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import SecretReference


class RestartRuntime:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.recover_intent: ApplicationDesiredState | None = None
        self._descriptor = ApplicationRuntimeDescriptor(
            runtime_id="reference.process",
            supported_service_runtimes=frozenset({ApplicationServiceRuntime.PROCESS}),
            capabilities=frozenset({"local"}),
        )

    @property
    def descriptor(self) -> ApplicationRuntimeDescriptor:
        return self._descriptor

    def prepare(self, request: ApplicationInstallRequest) -> ApplicationInstance:
        return ApplicationInstance(
            application_id=request.manifest.application_id,
            application_version=request.manifest.version,
            runtime_id=self.descriptor.runtime_id,
            desired_state=ApplicationDesiredState.STOPPED,
            observed_state=ApplicationObservedState.STOPPED,
            health=ApplicationHealthStatus.UNKNOWN,
            node_id=request.node_id,
            configuration=request.resolved_configuration(),
            secret_bindings=request.secret_bindings,
            volume_bindings=request.volume_bindings,
            service_states=(
                ApplicationServiceState(
                    service_id="app",
                    observed_state=ApplicationObservedState.STOPPED,
                    health=ApplicationHealthStatus.UNKNOWN,
                ),
            ),
        )

    def start(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        if self.fail_start:
            raise ApplicationRuntimeError("simulated start failure")
        return self._running(instance)

    def stop(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        return replace(
            instance,
            observed_state=ApplicationObservedState.STOPPED,
            health=ApplicationHealthStatus.UNKNOWN,
        )

    def restart(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        return self._running(instance)

    def remove(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        return replace(
            instance,
            observed_state=ApplicationObservedState.REMOVED,
            health=ApplicationHealthStatus.UNKNOWN,
        )

    def status(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        return instance

    def health(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationHealthStatus:
        return instance.health

    def endpoints(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> tuple[ApplicationEndpointResolution, ...]:
        return instance.endpoints

    def logs(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        *,
        service_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ApplicationLogEntry, ...]:
        return ()

    def reconcile(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        return self.recover(manifest, instance)

    def recover(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        self.recover_intent = instance.desired_state
        if instance.desired_state is ApplicationDesiredState.RUNNING:
            return self._running(instance)
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

    @staticmethod
    def _running(instance: ApplicationInstance) -> ApplicationInstance:
        return replace(
            instance,
            observed_state=ApplicationObservedState.RUNNING,
            health=ApplicationHealthStatus.HEALTHY,
            service_states=(
                ApplicationServiceState(
                    service_id="app",
                    observed_state=ApplicationObservedState.RUNNING,
                    health=ApplicationHealthStatus.HEALTHY,
                ),
            ),
            endpoints=(
                ApplicationEndpointResolution(
                    endpoint_ref="app.web",
                    uri="http://127.0.0.1:49152/",
                    exposure=ApplicationEndpointExposure.USER,
                ),
            ),
        )


def _manifest() -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Durable app",
        version="1.0.0",
        description="Durable Application Adapter fixture",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-m", "example"),
                endpoints=(
                    ApplicationEndpoint(
                        name="web",
                        target_port=8080,
                        exposure=ApplicationEndpointExposure.USER,
                        path="/",
                    ),
                ),
                mounts=(ApplicationVolumeMount(volume_name="workspace", target="/workspace"),),
                health_check=ApplicationHealthCheck(
                    kind=ApplicationHealthCheckKind.ENDPOINT,
                    endpoint_name="web",
                ),
            ),
        ),
        volumes=(ApplicationVolume(name="workspace", kind=ApplicationVolumeKind.WORKSPACE),),
        configuration=(
            ApplicationConfigurationField(
                name="workers",
                value_type=ApplicationConfigValueType.INTEGER,
                default=2,
                environment_variable="APP_WORKERS",
            ),
        ),
        secrets=(
            ApplicationSecretField(
                name="api_token",
                environment_variable="APP_API_TOKEN",
            ),
        ),
        resources=ApplicationResourceRequirements(
            cpu_cores=1.5,
            memory_bytes=512 * 1024 * 1024,
            architectures=("x86_64",),
            operating_systems=("linux",),
            required_capabilities=("process",),
        ),
        ui=ApplicationUi(
            endpoint_ref="app.web",
            open_mode=ApplicationUiOpenMode.EXTERNAL,
        ),
        resource_associations=(ApplicationResourceAssociation(media_types=("text/plain",)),),
        runtime_requirements=("local",),
    )


def _request(manifest: ApplicationManifest) -> ApplicationInstallRequest:
    return ApplicationInstallRequest(
        manifest=manifest,
        secret_bindings={
            "api_token": SecretReference(
                provider="platform",
                secret_id="secret/application-test",
                scope="application",
                metadata={"purpose": "runtime"},
            )
        },
        volume_bindings=(
            ApplicationVolumeBinding(
                volume_name="workspace",
                kind=ApplicationVolumeKind.WORKSPACE,
                source_ref=new_id("workspace"),
            ),
        ),
    )


def test_sqlite_repository_round_trips_canonical_application_state(tmp_path: Path) -> None:
    manifest = _manifest()
    request = _request(manifest)
    runtime = RestartRuntime()
    application = Application(
        manifest=manifest,
        runtime_id=runtime.descriptor.runtime_id,
        source_ref="registry://applications/durable-app",
        provenance={"registry": "local"},
    )
    instance = runtime.prepare(request)
    database_path = tmp_path / "applications.sqlite3"

    repository = SqliteApplicationRepository(database_path)
    repository.save_application(application)
    repository.save_instance(instance)

    reopened = SqliteApplicationRepository(database_path)
    assert reopened.schema_version == 1
    assert reopened.get_application(application.application_id, application.version) == application
    restored = reopened.get_instance(instance.instance_id)
    assert restored == instance
    assert restored.secret_bindings["api_token"].secret_id == "secret/application-test"
    assert dict(restored.secret_bindings["api_token"].metadata) == {"purpose": "runtime"}
    assert reopened.list_instances(application_id=manifest.application_id) == (instance,)


def test_restart_recovery_uses_persisted_running_intent_after_reopen(tmp_path: Path) -> None:
    manifest = _manifest()
    database_path = tmp_path / "applications.sqlite3"
    first_runtime = RestartRuntime(fail_start=True)
    first_repository = SqliteApplicationRepository(database_path)
    first_lifecycle = ApplicationLifecycleService(
        first_repository,
        ApplicationRuntimeRegistry((first_runtime,)),
    )
    installed = first_lifecycle.install(
        _request(manifest),
        runtime_id=first_runtime.descriptor.runtime_id,
    )

    with pytest.raises(ApplicationRuntimeError, match="simulated start failure"):
        first_lifecycle.start(installed.instance_id)

    failed = first_repository.get_instance(installed.instance_id)
    assert failed.desired_state is ApplicationDesiredState.RUNNING
    assert failed.observed_state is ApplicationObservedState.FAILED

    second_runtime = RestartRuntime()
    reopened = SqliteApplicationRepository(database_path)
    second_lifecycle = ApplicationLifecycleService(
        reopened,
        ApplicationRuntimeRegistry((second_runtime,)),
    )
    report = second_lifecycle.recover_all()

    assert report.failures == ()
    assert second_runtime.recover_intent is ApplicationDesiredState.RUNNING
    recovered = reopened.get_instance(installed.instance_id)
    assert recovered.desired_state is ApplicationDesiredState.RUNNING
    assert recovered.observed_state is ApplicationObservedState.RUNNING
    assert recovered.health is ApplicationHealthStatus.HEALTHY
    assert recovered.revision > failed.revision


def test_sqlite_repository_enforces_immutable_application_version(tmp_path: Path) -> None:
    manifest = _manifest()
    repository = SqliteApplicationRepository(tmp_path / "applications.sqlite3")
    original = Application(manifest=manifest, runtime_id="reference.process")
    repository.save_application(original)

    with pytest.raises(ContractError) as exc_info:
        repository.save_application(
            Application(
                manifest=manifest,
                runtime_id="another.runtime",
            )
        )

    assert exc_info.value.code is ErrorCode.CONFLICT


def test_sqlite_repository_rejects_orphan_instance(tmp_path: Path) -> None:
    manifest = _manifest()
    instance = RestartRuntime().prepare(_request(manifest))
    repository = SqliteApplicationRepository(tmp_path / "applications.sqlite3")

    with pytest.raises(ContractError) as exc_info:
        repository.save_instance(instance)

    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
