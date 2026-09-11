from __future__ import annotations

import pytest

from ai_multi_agent_platform.application_distribution import (
    BuildSpecification,
    BuildTarget,
    PackageType,
)
from ai_multi_agent_platform.application_distribution.execution import APPLICATION_BUILD_ACTION
from ai_multi_agent_platform.application_distribution.placement import job_requirements_for_target


def _target() -> BuildTarget:
    return BuildTarget(
        target_id="linux-x64",
        os_name="Linux",
        architecture="amd64",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.tar.gz",
        required_capabilities=("toolchain:python",),
    )


def test_application_target_translates_to_canonical_worker_requirements() -> None:
    specification = BuildSpecification(
        command=("python", "build.py"),
        targets=(_target(),),
        required_capabilities=("builder:archive",),
        resource_hints={
            "cpu_cores": 2,
            "ram_bytes": 1024,
            "storage_bytes": 4096,
            "runtime": "python3.13",
            "network_required": True,
            "allowed_trust_levels": ["trusted"],
            "locality_refs": ["project:demo"],
            "preferred_labels": ["build-cache"],
        },
    )

    requirements = job_requirements_for_target(specification, _target())

    assert requirements.os_name == "linux"
    assert requirements.architecture == "x86_64"
    assert requirements.capability_refs == (
        APPLICATION_BUILD_ACTION,
        "builder:archive",
        "toolchain:python",
    )
    assert requirements.cpu_cores_min == 2.0
    assert requirements.ram_min_bytes == 1024
    assert requirements.storage_min_bytes == 4096
    assert requirements.runtime == "python3.13"
    assert requirements.network_required is True
    assert requirements.allowed_trust_levels == ("trusted",)
    assert requirements.locality_refs == ("project:demo",)
    assert requirements.preferred_labels == ("build-cache",)


def test_application_target_rejects_conflicting_resource_hint_aliases() -> None:
    specification = BuildSpecification(
        command=("python", "build.py"),
        targets=(_target(),),
        resource_hints={"cpu_cores": 2, "cpu_cores_min": 1},
    )

    with pytest.raises(ValueError, match="cannot define both cpu_cores_min and cpu_cores"):
        job_requirements_for_target(specification, _target())
