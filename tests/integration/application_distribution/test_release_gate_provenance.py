from __future__ import annotations

import asyncio
import json

from ai_multi_agent_platform.application_distribution import (
    ApplicationArtifact,
    ApplicationRelease,
    ApplicationReleaseGateCoordinator,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    BuildTargetStatus,
    DeterministicGateCheck,
    GateStatus,
    PackageType,
    ReleaseChannel,
    ReleaseGateKind,
    ReleaseGateRequirement,
    ReleaseStatus,
    ReleaseVisibility,
    StaticReleaseGatePolicy,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import REDACTED


class _Files:
    async def verify_checksum(self, file_id: str, context: object) -> bool:
        del file_id, context
        return True


def _release(*, external_metadata: dict[str, object]) -> ApplicationRelease:
    target = BuildTarget(
        target_id="linux-x64",
        os_name="linux",
        architecture="x86_64",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.tar.gz",
    )
    task_id = new_id("task")
    run_id = new_id("run")
    artifact = ApplicationArtifact(
        artifact_id=new_id("artifact"),
        file_id=new_id("file"),
        target_id=target.target_id,
        filename="dist/app.tar.gz",
        package_type=target.package_type,
        media_type="application/gzip",
        sha256="a" * 64,
        build_task_id=task_id,
        build_run_id=run_id,
        external_metadata=external_metadata,  # type: ignore[arg-type]
    )
    specification = BuildSpecification(
        command=("python", "-m", "build"),
        targets=(target,),
        test_gates=("artifact-exists",),
    )
    return ApplicationRelease(
        application_id="provenance-app",
        display_name="Provenance App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PUBLIC,
        project_id=new_id("project"),
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        workspace_content_checksum="c" * 64,
        source_revision="0123456789abcdef0123456789abcdef01234567",
        build_specification=specification,
        creator_ref="user:tester",
        status=ReleaseStatus.READY,
        targets=(
            BuildTargetState(
                target=target,
                status=BuildTargetStatus.SUCCEEDED,
                task_id=task_id,
                run_id=run_id,
            ),
        ),
        artifacts=(artifact,),
    )


def _coordinator() -> ApplicationReleaseGateCoordinator:
    return ApplicationReleaseGateCoordinator(
        policy=StaticReleaseGatePolicy(
            (
                ReleaseGateRequirement(
                    name="artifact-exists",
                    kind=ReleaseGateKind.DETERMINISTIC,
                    target_id="linux-x64",
                    deterministic_check=DeterministicGateCheck.ARTIFACT_EXISTS,
                ),
            )
        ),
        files=_Files(),  # type: ignore[arg-type]
    )


def test_runtime_provenance_retains_safe_values_and_redacts_paths_and_secrets() -> None:
    async def scenario() -> None:
        release = _release(
            external_metadata={
                "worker_id": "worker-7",
                "executor_id": "local-executor",
                "runtime_version": None,
                "os": "linux",
                "architecture": "x86_64",
                "tool_versions": {
                    "python": "3.12.4",
                    "python_path": "/home/samu/.venv/bin/python",
                    "windows_tool_path": "C:\\Users\\samu\\tool.exe",
                    "api_key": "super-secret-key",
                },
                "environment_fingerprint": "TOKEN=raw-secret-token",
                "provider_private_path": "/tmp/must-not-be-projected",
            }
        )

        gate = (await _coordinator().reconcile(release))[0]

        assert gate.status is GateStatus.PASSED
        runtime = gate.details["runtime_provenance"]
        assert isinstance(runtime, dict)
        assert runtime["worker_id"] == "worker-7"
        assert runtime["executor_id"] == "local-executor"
        assert runtime["runtime_version"] is None
        assert runtime["os"] == "linux"
        assert runtime["architecture"] == "x86_64"
        assert "node_id" not in runtime
        assert "provider_private_path" not in runtime

        tool_versions = runtime["tool_versions"]
        assert isinstance(tool_versions, dict)
        assert tool_versions["python"] == "3.12.4"
        assert tool_versions["python_path"] == REDACTED
        assert tool_versions["windows_tool_path"] == REDACTED
        assert tool_versions["api_key"] == REDACTED
        assert runtime["environment_fingerprint"] == f"TOKEN={REDACTED}"

        serialized = json.dumps(dict(gate.details), sort_keys=True)
        assert "super-secret-key" not in serialized
        assert "raw-secret-token" not in serialized
        assert "/home/samu/.venv/bin/python" not in serialized
        assert "C:\\\\Users\\\\samu\\\\tool.exe" not in serialized
        assert "/tmp/must-not-be-projected" not in serialized

    asyncio.run(scenario())


def test_missing_runtime_metadata_remains_unknown_instead_of_being_fabricated() -> None:
    async def scenario() -> None:
        release = _release(external_metadata={"provider_note": "no runtime evidence available"})

        gate = (await _coordinator().reconcile(release))[0]

        assert gate.status is GateStatus.PASSED
        assert "runtime_provenance" not in gate.details

    asyncio.run(scenario())
