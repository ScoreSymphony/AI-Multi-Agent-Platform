from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    ApplicationBuildLifecycleBackend,
    ApplicationCommandExecutor,
    ApplicationDistributionService,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    InMemoryApplicationReleaseRepository,
    JsonApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
    control_plane,
)
from ai_multi_agent_platform.configuration import (
    LocalSecretProvider,
    SecretAccessContext,
    SecretMaterial,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.execution import ExecutionRequest
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    WorkspaceType,
)


_SECRET_VALUE = "fixture-secret-value-748"
_SECRET_CONSUMER = "service:application-build-secrets"
_SECRET_PURPOSE = "application_build"


class _RecordingSecretProvider(LocalSecretProvider):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[SecretAccessContext] = []

    async def resolve(
        self,
        reference: SecretReference,
        context: SecretAccessContext,
    ) -> SecretMaterial:
        self.contexts.append(context)
        return await super().resolve(reference, context)


def _target() -> BuildTarget:
    return BuildTarget(
        target_id="local-test",
        os_name="test",
        architecture="test",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.bin",
    )


def _reference(project_id: str) -> SecretReference:
    return SecretReference(
        provider="local-secrets",
        secret_id="application-build-token",
        scope=project_id,
    )


def test_build_specification_separates_safe_and_secret_environment() -> None:
    project_id = new_id("project")
    reference = _reference(project_id)
    specification = BuildSpecification(
        command=("tool", "build"),
        targets=(_target(),),
        environment={"BUILD_MODE": "release"},
        secret_environment={"PRIVATE_INDEX_TOKEN": reference},
    )

    assert dict(specification.environment) == {"BUILD_MODE": "release"}
    assert specification.secret_environment["PRIVATE_INDEX_TOKEN"] == reference

    with pytest.raises(ValueError, match="sensitive-looking"):
        BuildSpecification(
            command=("tool", "build"),
            targets=(_target(),),
            environment={"API_TOKEN": _SECRET_VALUE},
        )

    with pytest.raises(ValueError, match="must not define the same variable"):
        BuildSpecification(
            command=("tool", "build"),
            targets=(_target(),),
            environment={"BUILD_MODE": "release"},
            secret_environment={"BUILD_MODE": reference},
        )


def test_control_plane_build_spec_accepts_references_not_secret_values() -> None:
    project_id = new_id("project")
    specification = control_plane._build_spec(  # noqa: SLF001 - exact parser boundary regression
        {
            "command": ["tool", "build"],
            "targets": [
                {
                    "target_id": "local-test",
                    "os_name": "test",
                    "architecture": "test",
                    "package_type": "archive",
                    "output_path": "dist/app.bin",
                }
            ],
            "environment": {"BUILD_MODE": "release"},
            "secret_environment": {
                "PRIVATE_INDEX_TOKEN": {
                    "provider": "local-secrets",
                    "secret_id": "application-build-token",
                    "scope": project_id,
                }
            },
        },
        default_spec_id=new_id("build_spec"),
    )

    assert specification.environment["BUILD_MODE"] == "release"
    reference = specification.secret_environment["PRIVATE_INDEX_TOKEN"]
    assert reference.secret_id == "application-build-token"
    assert reference.scope == project_id

    with pytest.raises((ContractError, ValueError)):
        control_plane._build_spec(  # noqa: SLF001 - exact parser boundary regression
            {
                "command": ["tool", "build"],
                "targets": [
                    {
                        "target_id": "local-test",
                        "os_name": "test",
                        "architecture": "test",
                        "package_type": "archive",
                        "output_path": "dist/app.bin",
                    }
                ],
                "environment": {"API_TOKEN": _SECRET_VALUE},
            },
            default_spec_id=new_id("build_spec"),
        )


def test_application_release_repository_round_trips_secret_references_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        target = _target()
        release = ApplicationRelease(
            application_id="environment-app",
            display_name="Environment App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=new_id("workspace"),
            workspace_snapshot_id=new_id("workspace_snapshot"),
            workspace_content_checksum="a" * 64,
            source_revision="source-revision-748",
            build_specification=BuildSpecification(
                command=("tool", "build"),
                targets=(target,),
                environment={"BUILD_MODE": "release"},
                secret_environment={"PRIVATE_INDEX_TOKEN": _reference(project_id)},
            ),
            creator_ref="user:tester",
            targets=(BuildTargetState(target),),
        )
        path = tmp_path / "application-releases.json"
        repository = JsonApplicationReleaseRepository(path)
        await repository.save(release, expected_revision=0)

        restored = await JsonApplicationReleaseRepository(path).get(release.release_id)
        assert dict(restored.build_specification.environment) == {"BUILD_MODE": "release"}
        assert (
            restored.build_specification.secret_environment["PRIVATE_INDEX_TOKEN"].secret_id
            == "application-build-token"
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        serialized = json.dumps(document, sort_keys=True)
        assert "application-build-token" in serialized
        assert _SECRET_VALUE not in serialized

    asyncio.run(scenario())


def test_application_command_executor_filters_host_environment_and_redacts_secret_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "materialized-build"
        workspace.mkdir()
        monkeypatch.setenv("UNRELATED_HOST_SECRET", "must-not-be-inherited")
        executor = ApplicationCommandExecutor(tmp_path)
        request = ExecutionRequest(
            task_id=new_id("task"),
            run_id=new_id("run"),
            correlation_id="application-build-environment",
            action=APPLICATION_BUILD_ACTION,
            workspace=workspace.name,
            arguments={
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "import os; from pathlib import Path; "
                        "assert os.environ['BUILD_MODE'] == 'release'; "
                        "assert os.environ['PRIVATE_INDEX_TOKEN'] == 'fixture-secret-value-748'; "
                        "assert os.environ.get('UNRELATED_HOST_SECRET') is None; "
                        "print(os.environ['BUILD_MODE']); "
                        "print(os.environ['PRIVATE_INDEX_TOKEN']); "
                        "Path('dist').mkdir(); Path('dist/app.bin').write_bytes(b'ok')"
                    ),
                ],
                "output_path": "dist/app.bin",
            },
            environment={
                "BUILD_MODE": "release",
                "PRIVATE_INDEX_TOKEN": _SECRET_VALUE,
            },
            policy_context={"sensitive_environment_keys": ["PRIVATE_INDEX_TOKEN"]},
        )

        result = await executor.execute(request)

        assert result.status.value == "succeeded"
        assert "release" in result.stdout
        assert "[REDACTED]" in result.stdout
        assert _SECRET_VALUE not in result.stdout
        assert "must-not-be-inherited" not in result.stdout
        assert (workspace / "dist" / "app.bin").read_bytes() == b"ok"

    asyncio.run(scenario())


def test_application_build_resolves_secret_at_exact_run_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id="application-build-secret-vertical",
            owner_type="user",
            owner_id="tester",
            project_id=project_id,
        )
        data_context = DataAccessContext(operation=operation, actor_ref="user:tester")
        files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="tester"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=data_context,
        )
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id or "")
        reference = _reference(project_id)
        secrets = _RecordingSecretProvider()
        await secrets.create(
            reference,
            _SECRET_VALUE,
            purpose="application-build-test",
            allowed_consumers=(_SECRET_CONSUMER,),
            allowed_purposes=(_SECRET_PURPOSE,),
        )
        releases = InMemoryApplicationReleaseRepository()
        bindings = InMemoryRunWorkspaceBindingRepository()
        lifecycle = ApplicationBuildLifecycleBackend(
            releases,
            workspaces,
            files,
            bindings,
            ApplicationCommandExecutor(workspaces.materialization_root),
            secret_provider=secrets,
            secret_consumer_ref=_SECRET_CONSUMER,
            secret_purpose=_SECRET_PURPOSE,
        )
        kernel = PlatformKernel(
            orchestrator=ReferenceOrchestrator(),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        service = ApplicationDistributionService(
            releases,
            kernel=kernel,
            files=files,
            workspaces=workspaces,
            run_workspace_bindings=bindings,
        )
        release = await service.create_release(
            application_id="secret-app",
            display_name="Secret App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision="source-revision-748",
            build_specification=BuildSpecification(
                command=(
                    sys.executable,
                    "-c",
                    (
                        "import os; from pathlib import Path; "
                        "assert os.environ['BUILD_MODE'] == 'release'; "
                        "assert os.environ['PRIVATE_INDEX_TOKEN'] == 'fixture-secret-value-748'; "
                        "print(os.environ['PRIVATE_INDEX_TOKEN']); "
                        "Path('dist').mkdir(); "
                        "Path('dist/app.bin').write_bytes(b'scoped-secret-build')"
                    ),
                ),
                targets=(_target(),),
                environment={"BUILD_MODE": "release"},
                secret_environment={"PRIVATE_INDEX_TOKEN": reference},
            ),
            creator_ref="user:tester",
        )

        built = await service.request_build(
            release.release_id,
            target_id="local-test",
            idempotency_key="secret-build-1",
            actor_ref="user:tester",
        )

        assert built.status is ReleaseStatus.READY
        assert len(secrets.contexts) == 1
        access = secrets.contexts[0]
        target = built.targets[0]
        assert target.task_id is not None
        assert target.run_id is not None
        assert access.consumer_ref == _SECRET_CONSUMER
        assert access.project_id == project_id
        assert access.workspace_id == workspace.id
        assert access.task_id == target.task_id
        assert access.run_id == target.run_id
        assert access.action == APPLICATION_BUILD_ACTION
        assert access.capability_ref == APPLICATION_BUILD_ACTION
        assert access.purpose == _SECRET_PURPOSE

        run = await kernel.get_run(target.task_id, target.run_id)
        serialized_run = json.dumps(run.output, sort_keys=True)
        assert _SECRET_VALUE not in serialized_run
        assert "[REDACTED]" in serialized_run
        assert _SECRET_VALUE not in repr(built)

    asyncio.run(scenario())


def test_application_build_rejects_cross_project_secret_reference(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        other_project_id = new_id("project")
        release = ApplicationRelease(
            application_id="scope-app",
            display_name="Scope App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=new_id("workspace"),
            workspace_snapshot_id=new_id("workspace_snapshot"),
            workspace_content_checksum="b" * 64,
            source_revision="source-revision-748",
            build_specification=BuildSpecification(
                command=("tool", "build"),
                targets=(_target(),),
                secret_environment={"PRIVATE_INDEX_TOKEN": _reference(other_project_id)},
            ),
            creator_ref="user:tester",
        )
        lifecycle = ApplicationBuildLifecycleBackend(
            InMemoryApplicationReleaseRepository(),
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            InMemoryRunWorkspaceBindingRepository(),
            ApplicationCommandExecutor(tmp_path),
            secret_provider=_RecordingSecretProvider(),
        )

        with pytest.raises(ContractError) as raised:
            await lifecycle._build_environment(  # noqa: SLF001 - exact boundary regression test
                release,
                task_id=new_id("task"),
                run_id=new_id("run"),
                timeout_seconds=None,
            )

        assert raised.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())
