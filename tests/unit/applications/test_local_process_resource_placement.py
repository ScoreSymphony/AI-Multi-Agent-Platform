from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.applications import (
    ApplicationInstallRequest,
    ApplicationManifest,
    ApplicationResourceRequirements,
    ApplicationService,
    ApplicationServiceRuntime,
    LocalApplicationHostProfile,
    LocalProcessApplicationRuntime,
)
from ai_multi_agent_platform.applications.runtime import ApplicationPreparationError
from ai_multi_agent_platform.domain import new_id


def _manifest(resources: ApplicationResourceRequirements) -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Local resource placement fixture",
        version="1.0.0",
        description="Validates local host suitability before process preparation",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-c", "pass"),
            ),
        ),
        resources=resources,
        runtime_requirements=("local", "process"),
    )


def _host(
    *,
    architecture: str = "x86_64",
    operating_system: str = "Linux",
    cpu_cores: float | None = 8.0,
    memory_bytes: int | None = 16_000,
    disk_available_bytes: int | None = 100_000,
    gpu_count: int = 1,
    capabilities: frozenset[str] = frozenset({"avx2"}),
    labels: frozenset[str] = frozenset({"application-ready"}),
) -> LocalApplicationHostProfile:
    return LocalApplicationHostProfile(
        architecture=architecture,
        operating_system=operating_system,
        cpu_cores=cpu_cores,
        memory_bytes=memory_bytes,
        disk_available_bytes=disk_available_bytes,
        gpu_count=gpu_count,
        capabilities=capabilities,
        labels=labels,
    )


def _prepare(
    resources: ApplicationResourceRequirements,
    *,
    host: LocalApplicationHostProfile | None = None,
):
    runtime = LocalProcessApplicationRuntime(host_profile=host or _host())
    return asyncio.run(runtime.prepare(ApplicationInstallRequest(manifest=_manifest(resources))))


def test_local_process_runtime_accepts_host_satisfying_declared_resources() -> None:
    instance = _prepare(
        ApplicationResourceRequirements(
            cpu_cores=4,
            memory_bytes=8_000,
            gpu_count=1,
            disk_bytes=50_000,
            architectures=("amd64",),
            operating_systems=("linux",),
            required_capabilities=("local", "process", "avx2"),
            required_labels=("application-ready",),
        )
    )

    assert instance.observed_state.value == "stopped"


@pytest.mark.parametrize(
    ("resources", "host", "rejection"),
    [
        (
            ApplicationResourceRequirements(cpu_cores=9),
            _host(),
            "cpu",
        ),
        (
            ApplicationResourceRequirements(memory_bytes=16_001),
            _host(),
            "memory",
        ),
        (
            ApplicationResourceRequirements(memory_bytes=1),
            _host(memory_bytes=None),
            "memory",
        ),
        (
            ApplicationResourceRequirements(disk_bytes=100_001),
            _host(),
            "disk",
        ),
        (
            ApplicationResourceRequirements(disk_bytes=1),
            _host(disk_available_bytes=None),
            "disk",
        ),
        (
            ApplicationResourceRequirements(gpu_count=2),
            _host(),
            "gpu",
        ),
        (
            ApplicationResourceRequirements(architectures=("arm64",)),
            _host(),
            "architecture",
        ),
        (
            ApplicationResourceRequirements(operating_systems=("windows",)),
            _host(),
            "operating_system",
        ),
        (
            ApplicationResourceRequirements(required_capabilities=("cuda",)),
            _host(),
            "capabilities",
        ),
        (
            ApplicationResourceRequirements(required_labels=("gpu-node",)),
            _host(),
            "labels",
        ),
    ],
)
def test_local_process_runtime_rejects_unsuitable_host(
    resources: ApplicationResourceRequirements,
    host: LocalApplicationHostProfile,
    rejection: str,
) -> None:
    with pytest.raises(
        ApplicationPreparationError,
        match=rf"resource requirements: .*{rejection}",
    ):
        _prepare(resources, host=host)


def test_local_host_profile_normalizes_common_architecture_and_os_aliases() -> None:
    host = _host(architecture="aarch64", operating_system="Darwin")

    assert host.architecture == "arm64"
    assert host.operating_system == "macos"
