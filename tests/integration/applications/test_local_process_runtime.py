from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ai_multi_agent_platform.applications.local_process_runtime import (
    LocalProcessApplicationRuntime,
)
from ai_multi_agent_platform.applications.models import (
    ApplicationConfigurationField,
    ApplicationConfigValueType,
    ApplicationDesiredState,
    ApplicationHealthCheck,
    ApplicationHealthCheckKind,
    ApplicationHealthStatus,
    ApplicationInstallRequest,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationSecretField,
    ApplicationService,
    ApplicationServiceRuntime,
)
from ai_multi_agent_platform.applications.repository import InMemoryApplicationRepository
from ai_multi_agent_platform.applications.runtime import (
    ApplicationPreparationError,
    ApplicationRuntimeUnavailableError,
)
from ai_multi_agent_platform.applications.service import (
    ApplicationLifecycleService,
    ApplicationRuntimeRegistry,
)
from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import SecretReference

pytestmark = pytest.mark.asyncio


def _manifest(*services: ApplicationService) -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Local process fixture",
        version="1.0.0",
        description="Real subprocess lifecycle fixture",
        services=services,
        configuration=(
            ApplicationConfigurationField(
                name="label",
                value_type=ApplicationConfigValueType.STRING,
                default="default-label",
                environment_variable="APP_LABEL",
            ),
        ),
        runtime_requirements=("local", "process"),
    )


def _secret_manifest(service: ApplicationService) -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Secret process fixture",
        version="1.0.0",
        description="Secret projection lifecycle fixture",
        services=(service,),
        secrets=(
            ApplicationSecretField(
                name="token",
                environment_variable="APP_SECRET",
            ),
        ),
        runtime_requirements=("local", "process", "secret_environment"),
    )


async def _installed(
    manifest: ApplicationManifest,
    *,
    configuration: dict[str, str] | None = None,
    secret_bindings: dict[str, SecretReference] | None = None,
    runtime: LocalProcessApplicationRuntime | None = None,
) -> tuple[
    LocalProcessApplicationRuntime,
    ApplicationLifecycleService,
    InMemoryApplicationRepository,
    str,
]:
    runtime = runtime or LocalProcessApplicationRuntime(stop_timeout_seconds=1.0)
    repository = InMemoryApplicationRepository()
    lifecycle = ApplicationLifecycleService(
        repository,
        ApplicationRuntimeRegistry((runtime,)),
    )
    instance = await lifecycle.install(
        ApplicationInstallRequest(
            manifest=manifest,
            configuration=configuration or {},
            secret_bindings=secret_bindings or {},
        ),
        runtime_id=runtime.descriptor.runtime_id,
    )
    return runtime, lifecycle, repository, instance.instance_id


async def _wait_for_log(
    lifecycle: ApplicationLifecycleService,
    instance_id: str,
    expected: str,
) -> None:
    await _wait_for_log_count(lifecycle, instance_id, expected, count=1)


async def _wait_for_log_count(
    lifecycle: ApplicationLifecycleService,
    instance_id: str,
    expected: str,
    *,
    count: int,
) -> None:
    for _ in range(50):
        entries = await lifecycle.logs(instance_id)
        observed = sum(entry.message == expected for entry in entries)
        if observed >= count:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"log entry count was not observed: {expected!r} >= {count}")


async def _wait_for_failed_status(
    lifecycle: ApplicationLifecycleService,
    instance_id: str,
) -> None:
    for _ in range(100):
        observed = await lifecycle.status(instance_id)
        if observed.observed_state is ApplicationObservedState.FAILED:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("application did not transition to failed state")


async def test_local_process_runtime_runs_real_process_and_projects_configuration() -> None:
    script = (
        "import os,sys,time; "
        "print('stdout:' + os.environ['APP_LABEL'], flush=True); "
        "print('stderr-line', file=sys.stderr, flush=True); "
        "time.sleep(30)"
    )
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=("-c", script),
    )
    _, lifecycle, repository, instance_id = await _installed(
        _manifest(service),
        configuration={"label": "configured"},
    )

    try:
        running = await lifecycle.start(instance_id)
        assert running.desired_state is ApplicationDesiredState.RUNNING
        assert running.observed_state is ApplicationObservedState.RUNNING
        assert running.health is ApplicationHealthStatus.HEALTHY

        await _wait_for_log(lifecycle, instance_id, "stdout:configured")
        await _wait_for_log(lifecycle, instance_id, "stderr-line")
        entries = await lifecycle.logs(instance_id)
        assert {entry.level for entry in entries} >= {"info", "error"}

        stopped = await lifecycle.stop(instance_id)
        assert stopped.desired_state is ApplicationDesiredState.STOPPED
        assert stopped.observed_state is ApplicationObservedState.STOPPED
        assert stopped.health is ApplicationHealthStatus.UNKNOWN
        assert repository.get_instance(instance_id) == stopped
    finally:
        current = repository.get_instance(instance_id)
        if current.desired_state is not ApplicationDesiredState.STOPPED:
            await lifecycle.stop(instance_id)


async def test_local_process_runtime_waits_for_dependency_health_before_starting_dependents(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "dependency-ready"
    marker_literal = repr(str(marker))
    dependency_script = (
        "import pathlib,time; "
        f"pathlib.Path({marker_literal}).write_text('ready'); "
        "print('dependency-ready', flush=True); time.sleep(30)"
    )
    probe_script = (
        f"import pathlib,sys; sys.exit(0 if pathlib.Path({marker_literal}).exists() else 1)"
    )
    dependent_script = (
        "import pathlib,sys,time; "
        f"sys.exit(7) if not pathlib.Path({marker_literal}).exists() else None; "
        "print('dependent-ready', flush=True); time.sleep(30)"
    )
    dependency = ApplicationService(
        service_id="dependency",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=("-c", dependency_script),
        health_check=ApplicationHealthCheck(
            kind=ApplicationHealthCheckKind.COMMAND,
            command=(sys.executable, "-c", probe_script),
            interval_seconds=0.02,
            timeout_seconds=1.0,
            retries=50,
        ),
    )
    dependent = ApplicationService(
        service_id="dependent",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=("-c", dependent_script),
        depends_on=("dependency",),
    )
    _, lifecycle, repository, instance_id = await _installed(_manifest(dependency, dependent))

    try:
        running = await lifecycle.start(instance_id)
        assert running.observed_state is ApplicationObservedState.RUNNING
        assert tuple(state.service_id for state in running.service_states) == (
            "dependency",
            "dependent",
        )
        await _wait_for_log(lifecycle, instance_id, "dependency-ready")
        await _wait_for_log(lifecycle, instance_id, "dependent-ready")
    finally:
        await lifecycle.stop(instance_id)
        assert (
            repository.get_instance(instance_id).observed_state is ApplicationObservedState.STOPPED
        )


async def test_local_process_runtime_restart_preserves_canonical_instance_identity() -> None:
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=("-c", "import time; print('started', flush=True); time.sleep(30)"),
    )
    _, lifecycle, _, instance_id = await _installed(_manifest(service))

    try:
        first = await lifecycle.start(instance_id)
        await _wait_for_log_count(lifecycle, instance_id, "started", count=1)
        restarted = await lifecycle.restart(instance_id)
        await _wait_for_log_count(lifecycle, instance_id, "started", count=2)
        assert restarted.instance_id == first.instance_id == instance_id
        assert restarted.desired_state is ApplicationDesiredState.RUNNING
        assert restarted.observed_state is ApplicationObservedState.RUNNING
    finally:
        await lifecycle.stop(instance_id)


async def test_local_process_runtime_projects_secret_and_redacts_process_logs() -> None:
    secret_value = "runtime-secret-do-not-log"
    provider = LocalSecretProvider()
    reference = SecretReference(
        provider="local-secrets",
        secret_id="runtime-token",
        scope="application-runtime-test",
    )
    await provider.create(
        reference,
        secret_value,
        purpose="application_runtime",
        allowed_consumers=("service:application-runtime",),
        allowed_purposes=("application_runtime",),
    )
    runtime = LocalProcessApplicationRuntime(
        stop_timeout_seconds=1.0,
        secret_provider=provider,
        secret_lifetime_seconds=30,
    )
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=(
            "-c",
            "import os,time; print('secret:' + os.environ['APP_SECRET'], flush=True); "
            "time.sleep(30)",
        ),
    )
    _, lifecycle, repository, instance_id = await _installed(
        _secret_manifest(service),
        secret_bindings={"token": reference},
        runtime=runtime,
    )

    try:
        running = await lifecycle.start(instance_id)
        assert running.observed_state is ApplicationObservedState.RUNNING
        for _ in range(50):
            entries = await lifecycle.logs(instance_id)
            if any(entry.message.startswith("secret:") for entry in entries):
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("secret projection log line was not observed")
        entries = await lifecycle.logs(instance_id)
        rendered = "\n".join(entry.message for entry in entries)
        assert "secret:" in rendered
        assert secret_value not in rendered
        persisted = repository.get_instance(instance_id)
        assert persisted.secret_bindings["token"] == reference
        assert secret_value not in repr(persisted)
    finally:
        await lifecycle.stop(instance_id)


async def test_local_process_runtime_rejects_secret_binding_without_provider() -> None:
    reference = SecretReference(
        provider="local-secrets",
        secret_id="runtime-token",
        scope="application-runtime-test",
    )
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-c", "pass"),
    )
    runtime = LocalProcessApplicationRuntime()

    with pytest.raises(ApplicationPreparationError, match="requires a SecretProvider"):
        await runtime.prepare(
            ApplicationInstallRequest(
                manifest=_secret_manifest(service),
                secret_bindings={"token": reference},
            )
        )


async def test_local_process_runtime_stops_process_when_secret_lease_expires() -> None:
    provider = LocalSecretProvider()
    reference = SecretReference(
        provider="local-secrets",
        secret_id="expiring-token",
        scope="application-runtime-test",
    )
    await provider.create(
        reference,
        "lease-bound-secret",
        purpose="application_runtime",
        allowed_consumers=("service:application-runtime",),
        allowed_purposes=("application_runtime",),
    )
    runtime = LocalProcessApplicationRuntime(
        stop_timeout_seconds=1.0,
        secret_provider=provider,
        secret_lifetime_seconds=1,
    )
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=("-c", "import time; print('running', flush=True); time.sleep(30)"),
    )
    _, lifecycle, repository, instance_id = await _installed(
        _secret_manifest(service),
        secret_bindings={"token": reference},
        runtime=runtime,
    )

    try:
        running = await lifecycle.start(instance_id)
        assert running.observed_state is ApplicationObservedState.RUNNING
        await _wait_for_failed_status(lifecycle, instance_id)
        failed = repository.get_instance(instance_id)
        assert failed.desired_state is ApplicationDesiredState.RUNNING
        assert failed.observed_state is ApplicationObservedState.FAILED
        assert failed.health is ApplicationHealthStatus.UNHEALTHY
        assert all(
            state.message == "application secret lease expired" for state in failed.service_states
        )
    finally:
        await lifecycle.stop(instance_id)


async def test_local_process_runtime_recovery_fails_closed_without_process_ownership() -> None:
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=("-c", "import time; time.sleep(30)"),
    )
    first_runtime, first_lifecycle, repository, instance_id = await _installed(_manifest(service))
    running = await first_lifecycle.start(instance_id)
    assert running.desired_state is ApplicationDesiredState.RUNNING

    second_runtime = LocalProcessApplicationRuntime(stop_timeout_seconds=1.0)
    second_lifecycle = ApplicationLifecycleService(
        repository,
        ApplicationRuntimeRegistry((second_runtime,)),
    )
    try:
        report = await second_lifecycle.recover_all()
        assert len(report.failures) == 1
        assert "refusing unsafe PID-only re-adoption" in report.failures[0].message
        failed = repository.get_instance(instance_id)
        assert failed.desired_state is ApplicationDesiredState.RUNNING
        assert failed.observed_state is ApplicationObservedState.FAILED
        assert failed.health is ApplicationHealthStatus.UNHEALTHY
    finally:
        current = repository.get_instance(instance_id)
        stopped = await first_runtime.stop(
            _manifest(service),
            current,
        )
        assert stopped.observed_state is ApplicationObservedState.STOPPED


async def test_local_process_runtime_rejects_unwired_node_placement() -> None:
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-c", "pass"),
    )
    manifest = _manifest(service)
    runtime = LocalProcessApplicationRuntime()

    with pytest.raises(ApplicationPreparationError, match="Node placement"):
        await runtime.prepare(
            ApplicationInstallRequest(
                manifest=manifest,
                node_id=new_id("node"),
            )
        )


async def test_local_process_runtime_recover_directly_reports_lost_ownership() -> None:
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-c", "pass"),
    )
    manifest = _manifest(service)
    runtime = LocalProcessApplicationRuntime()
    instance = await runtime.prepare(ApplicationInstallRequest(manifest=manifest))
    running_intent = instance.__class__(
        application_id=instance.application_id,
        application_version=instance.application_version,
        runtime_id=instance.runtime_id,
        desired_state=ApplicationDesiredState.RUNNING,
        observed_state=ApplicationObservedState.FAILED,
        health=ApplicationHealthStatus.UNHEALTHY,
        instance_id=instance.instance_id,
        configuration=instance.configuration,
        service_states=instance.service_states,
    )

    with pytest.raises(ApplicationRuntimeUnavailableError, match="PID-only"):
        await runtime.recover(manifest, running_intent)
