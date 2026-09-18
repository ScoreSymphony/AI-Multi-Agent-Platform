from __future__ import annotations

import asyncio
import sys

import pytest

from ai_multi_agent_platform.applications import (
    ApplicationConfigurationField,
    ApplicationConfigValueType,
    ApplicationDesiredState,
    ApplicationInstallRequest,
    ApplicationLifecycleService,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationRuntimeRegistry,
    ApplicationService,
    ApplicationServiceRuntime,
    InMemoryApplicationRepository,
    LocalProcessApplicationRuntime,
)
from ai_multi_agent_platform.domain import new_id

pytestmark = pytest.mark.asyncio


async def _wait_for_log(
    lifecycle: ApplicationLifecycleService,
    instance_id: str,
    message: str,
) -> None:
    for _ in range(100):
        if any(entry.message == message for entry in await lifecycle.logs(instance_id)):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"application log entry was not observed: {message!r}")


async def test_running_local_process_applies_mutable_configuration_via_restart() -> None:
    manifest = ApplicationManifest(
        application_id=new_id("application"),
        name="Mutable configuration fixture",
        version="1.0.0",
        description="Proves running configuration convergence",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=(sys.executable, "-u"),
                command=(
                    "-c",
                    "import os,time; print('label:' + os.environ['APP_LABEL'], flush=True); "
                    "time.sleep(30)",
                ),
            ),
        ),
        configuration=(
            ApplicationConfigurationField(
                name="label",
                value_type=ApplicationConfigValueType.STRING,
                environment_variable="APP_LABEL",
            ),
        ),
        runtime_requirements=("local", "process"),
    )
    runtime = LocalProcessApplicationRuntime(stop_timeout_seconds=1.0)
    repository = InMemoryApplicationRepository()
    lifecycle = ApplicationLifecycleService(
        repository,
        ApplicationRuntimeRegistry((runtime,)),
    )
    installed = await lifecycle.install(
        ApplicationInstallRequest(
            manifest=manifest,
            configuration={"label": "before"},
        ),
        runtime_id=runtime.descriptor.runtime_id,
    )

    try:
        running = await lifecycle.start(installed.instance_id)
        await _wait_for_log(lifecycle, installed.instance_id, "label:before")

        configured = await lifecycle.configure(
            installed.instance_id,
            {"label": "after"},
        )
        await _wait_for_log(lifecycle, installed.instance_id, "label:after")

        assert configured.instance_id == installed.instance_id
        assert configured.configuration["label"] == "after"
        assert configured.desired_state is ApplicationDesiredState.RUNNING
        assert configured.observed_state is ApplicationObservedState.RUNNING
        assert repository.get_instance(installed.instance_id) == configured
        assert running.instance_id == configured.instance_id
    finally:
        current = repository.get_instance(installed.instance_id)
        if current.desired_state is not ApplicationDesiredState.STOPPED:
            await lifecycle.stop(installed.instance_id)
