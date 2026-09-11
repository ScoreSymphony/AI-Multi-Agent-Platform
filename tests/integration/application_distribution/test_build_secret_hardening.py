from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    ApplicationBuildLifecycleBackend,
    ApplicationDistributionService,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    BuildTargetStatus,
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
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, new_id
from ai_multi_agent_platform.execution import (
    ExecutionRequest,
    ExecutionResult,
    Executor,
    ExecutorDescriptor,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    WorkspaceType,
)

_SECRET_VALUE = "fixture-secret-value-748-hardening"
_SECRET_CONSUMER = "service:application-build-secrets"
_SECRET_PURPOSE = "application_build"


class _FailIfExecuted(Executor):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id="must-not-run",
            capabilities=(APPLICATION_BUILD_ACTION,),
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        del request
        self.calls += 1
        raise AssertionError("build command must not execute after secret resolution failure")


class _ContractFailureSecretProvider(LocalSecretProvider):
    def __init__(self, *, code: ErrorCode, message: str) -> None:
        super().__init__()
        self._code = code
        self._message = message

    async def resolve(
        self,
        reference: SecretReference,
        context: SecretAccessContext,
    ) -> SecretMaterial:
        del reference, context
        raise ContractError(self._code, self._message)


def _target() -> BuildTarget:
    return BuildTarget(
        target_id="local-test",
        os_name="test",
        architecture="test",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.bin",
    )


def _reference(project_id: str, *, secret_id: str = "hardening-build-secret") -> SecretReference:
    return SecretReference(
        provider="local-secrets",
        secret_id=secret_id,
        scope=project_id,
    )


def test_execution_request_repr_omits_ephemeral_environment() -> None:
    request = ExecutionRequest(
        task_id=new_id("task"),
        run_id=new_id("run"),
        correlation_id="secret-diagnostic-repr",
        action=APPLICATION_BUILD_ACTION,
        workspace="materialized-build",
        environment={"PRIVATE_INDEX_TOKEN": _SECRET_VALUE},
    )

    rendered = repr(request)

    assert _SECRET_VALUE not in rendered
    assert "PRIVATE_INDEX_TOKEN" not in rendered
    assert "environment=" not in rendered


@pytest.mark.parametrize(
    ("command", "resource_hints", "message"),
    (
        (("tool", "API_TOKEN=plaintext-value"), {}, "sensitive environment assignments"),
        (("tool", "build"), {"api_token": "plaintext-value"}, "resource_hints"),
    ),
)
def test_build_specification_rejects_sensitive_canonical_inputs(
    command: tuple[str, ...],
    resource_hints: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        BuildSpecification(
            command=command,
            targets=(_target(),),
            resource_hints=resource_hints,
        )


def test_control_plane_projection_contains_reference_but_not_resolved_material() -> None:
    project_id = new_id("project")
    reference = _reference(project_id)
    release = ApplicationRelease(
        application_id="secret-api-app",
        display_name="Secret API App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PRIVATE,
        project_id=project_id,
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        workspace_content_checksum="e" * 64,
        source_revision="source-revision-secret-api",
        build_specification=BuildSpecification(
            command=("tool", "build"),
            targets=(_target(),),
            secret_environment={"PRIVATE_INDEX_TOKEN": reference},
        ),
        creator_ref="user:tester",
    )

    serialized = json.dumps(control_plane._resource(release), sort_keys=True)  # noqa: SLF001

    assert reference.secret_id in serialized
    assert _SECRET_VALUE not in serialized


@pytest.mark.parametrize(
    ("case", "code", "message"),
    (
        ("revoked", ErrorCode.FORBIDDEN, "secret is revoked"),
        ("consumer", ErrorCode.FORBIDDEN, "consumer is not allowed to resolve secret"),
        ("purpose", ErrorCode.FORBIDDEN, "purpose is not allowed to resolve secret"),
    ),
)
def test_secret_resolution_denials_become_terminal_canonical_build_failures(
    tmp_path: Path,
    case: str,
    code: ErrorCode,
    message: str,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id=f"application-build-{case}-secret",
            owner_type="user",
            owner_id="tester",
            project_id=project_id,
        )
        data_context = DataAccessContext(operation=operation, actor_ref="user:tester")
        files = LocalFileProvider(tmp_path / case / "files", tmp_path / case / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / case / "workspaces", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="tester"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=data_context,
        )
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id or "")
        reference = _reference(project_id, secret_id=f"{case}-build-secret")
        releases = InMemoryApplicationReleaseRepository()
        bindings = InMemoryRunWorkspaceBindingRepository()
        executor = _FailIfExecuted()
        lifecycle = ApplicationBuildLifecycleBackend(
            releases,
            workspaces,
            files,
            bindings,
            executor,
            secret_provider=_ContractFailureSecretProvider(code=code, message=message),
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
            application_id=f"{case}-secret-app",
            display_name=f"{case.title()} Secret App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision=f"source-revision-{case}-secret",
            build_specification=BuildSpecification(
                command=("must-not-run",),
                targets=(_target(),),
                secret_environment={"PRIVATE_INDEX_TOKEN": reference},
            ),
            creator_ref="user:tester",
        )

        failed = await service.request_build(
            release.release_id,
            target_id="local-test",
            idempotency_key=f"{case}-secret-build",
            actor_ref="user:tester",
        )

        assert executor.calls == 0
        assert failed.status is ReleaseStatus.FAILED
        target = failed.targets[0]
        assert target.status is BuildTargetStatus.FAILED
        assert target.task_id is not None
        assert target.run_id is not None
        run = await kernel.get_run(target.task_id, target.run_id)
        assert run.status is RunStatus.FAILED
        assert run.output["output"] == {
            "contract_error": {"code": code.value, "retryable": False}
        }
        serialized = repr(run.output)
        assert reference.secret_id not in serialized
        assert "PRIVATE_INDEX_TOKEN" not in serialized
        assert _SECRET_VALUE not in serialized

    asyncio.run(scenario())


def test_cross_project_secret_becomes_terminal_canonical_build_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        other_project_id = new_id("project")
        operation = OperationContext(
            correlation_id="application-build-cross-project-secret",
            owner_type="user",
            owner_id="tester",
            project_id=project_id,
        )
        data_context = DataAccessContext(operation=operation, actor_ref="user:tester")
        files = LocalFileProvider(tmp_path / "cross" / "files", tmp_path / "cross" / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "cross" / "workspaces", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="tester"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=data_context,
        )
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id or "")
        reference = _reference(other_project_id, secret_id="cross-project-build-secret")
        releases = InMemoryApplicationReleaseRepository()
        bindings = InMemoryRunWorkspaceBindingRepository()
        executor = _FailIfExecuted()
        lifecycle = ApplicationBuildLifecycleBackend(
            releases,
            workspaces,
            files,
            bindings,
            executor,
            secret_provider=LocalSecretProvider(),
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
            application_id="cross-project-secret-app",
            display_name="Cross Project Secret App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision="source-revision-cross-project-secret",
            build_specification=BuildSpecification(
                command=("must-not-run",),
                targets=(_target(),),
                secret_environment={"PRIVATE_INDEX_TOKEN": reference},
            ),
            creator_ref="user:tester",
        )

        failed = await service.request_build(
            release.release_id,
            target_id="local-test",
            idempotency_key="cross-project-secret-build",
            actor_ref="user:tester",
        )

        assert executor.calls == 0
        assert failed.status is ReleaseStatus.FAILED
        target = failed.targets[0]
        assert target.task_id is not None
        assert target.run_id is not None
        run = await kernel.get_run(target.task_id, target.run_id)
        assert run.status is RunStatus.FAILED
        assert run.output["output"] == {
            "contract_error": {"code": ErrorCode.FORBIDDEN.value, "retryable": False}
        }
        serialized = repr(run.output)
        assert reference.secret_id not in serialized
        assert "PRIVATE_INDEX_TOKEN" not in serialized

    asyncio.run(scenario())


def test_restart_recovery_never_requires_or_persists_resolved_secret(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        target = _target()
        task_id = new_id("task")
        run_id = new_id("run")
        reference = _reference(project_id, secret_id="restart-build-secret")
        release = ApplicationRelease(
            application_id="restart-secret-app",
            display_name="Restart Secret App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=new_id("workspace"),
            workspace_snapshot_id=new_id("workspace_snapshot"),
            workspace_content_checksum="f" * 64,
            source_revision="source-revision-restart-secret",
            build_specification=BuildSpecification(
                command=("tool", "build"),
                targets=(target,),
                secret_environment={"PRIVATE_INDEX_TOKEN": reference},
            ),
            creator_ref="user:tester",
            status=ReleaseStatus.BUILDING,
            targets=(
                BuildTargetState(
                    target,
                    status=BuildTargetStatus.RUNNING,
                    task_id=task_id,
                    run_id=run_id,
                ),
            ),
        )
        secrets = LocalSecretProvider()
        await secrets.create(
            reference,
            _SECRET_VALUE,
            purpose="application-build-test",
            allowed_consumers=(_SECRET_CONSUMER,),
            allowed_purposes=(_SECRET_PURPOSE,),
        )
        repository_path = tmp_path / "application-releases.json"
        repository = JsonApplicationReleaseRepository(repository_path)
        lifecycle = ApplicationBuildLifecycleBackend(
            repository,
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            InMemoryRunWorkspaceBindingRepository(),
            _FailIfExecuted(),
            secret_provider=secrets,
            secret_consumer_ref=_SECRET_CONSUMER,
            secret_purpose=_SECRET_PURPOSE,
        )
        environment, _ = await lifecycle._build_environment(  # noqa: SLF001
            release,
            task_id=task_id,
            run_id=run_id,
            timeout_seconds=None,
        )
        assert environment["PRIVATE_INDEX_TOKEN"] == _SECRET_VALUE
        await repository.save(release, expected_revision=0)
        assert _SECRET_VALUE not in repository_path.read_text(encoding="utf-8")

        restarted_repository = JsonApplicationReleaseRepository(repository_path)
        executor = _FailIfExecuted()
        restarted_lifecycle = ApplicationBuildLifecycleBackend(
            restarted_repository,
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            InMemoryRunWorkspaceBindingRepository(),
            executor,
            secret_provider=None,
        )
        recovered = await restarted_lifecycle.get(
            run_id,
            OperationContext(
                correlation_id="application-build-secret-restart",
                project_id=project_id,
            ),
        )

        assert recovered.status is RunStatus.FAILED
        assert executor.calls == 0
        assert "refusing implicit re-execution" in str(recovered.output.get("stderr"))
        restored = await restarted_repository.get(release.release_id)
        assert _SECRET_VALUE not in repr(restored)
        assert restored.build_specification.secret_environment["PRIVATE_INDEX_TOKEN"] == reference

    asyncio.run(scenario())
