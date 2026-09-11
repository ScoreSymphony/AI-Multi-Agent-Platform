from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    ApplicationBuildLifecycleBackend,
    ApplicationCommandExecutor,
    ApplicationDistributionService,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    InMemoryApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
    release_manifest,
)
from ai_multi_agent_platform.configuration import (
    LocalSecretProvider,
    SecretAccessContext,
    SecretMaterial,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    WorkspaceType,
)

_SECRET_VALUE = "fixture-secret-value-748-negative"
_SECRET_CONSUMER = "service:application-build-secrets"
_SECRET_PURPOSE = "application_build"


class _CountingSecretProvider(LocalSecretProvider):
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
        secret_id="application-build-negative-token",
        scope=project_id,
    )


def _release(project_id: str, reference: SecretReference) -> ApplicationRelease:
    return ApplicationRelease(
        application_id="secret-negative-app",
        display_name="Secret Negative App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PRIVATE,
        project_id=project_id,
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        workspace_content_checksum="c" * 64,
        source_revision="source-revision-748-negative",
        build_specification=BuildSpecification(
            command=("tool", "build"),
            targets=(_target(),),
            environment={"BUILD_MODE": "release"},
            secret_environment={"PRIVATE_INDEX_TOKEN": reference},
        ),
        creator_ref="user:tester",
    )


def _lifecycle(
    tmp_path: Path,
    *,
    secret_provider: LocalSecretProvider | None,
    consumer_ref: str = _SECRET_CONSUMER,
    purpose: str = _SECRET_PURPOSE,
) -> ApplicationBuildLifecycleBackend:
    return ApplicationBuildLifecycleBackend(
        InMemoryApplicationReleaseRepository(),
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        InMemoryRunWorkspaceBindingRepository(),
        ApplicationCommandExecutor(tmp_path),
        secret_provider=secret_provider,
        secret_consumer_ref=consumer_ref,
        secret_purpose=purpose,
    )


def test_secret_environment_requires_secret_provider(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        release = _release(project_id, _reference(project_id))
        lifecycle = _lifecycle(tmp_path, secret_provider=None)

        with pytest.raises(ContractError) as raised:
            await lifecycle._build_environment(  # noqa: SLF001 - exact boundary regression test
                release,
                task_id=new_id("task"),
                run_id=new_id("run"),
                timeout_seconds=None,
            )

        assert raised.value.code is ErrorCode.UNAVAILABLE

    asyncio.run(scenario())


def test_revoked_secret_fails_closed_before_build_dispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        reference = _reference(project_id)
        secrets = LocalSecretProvider()
        await secrets.create(
            reference,
            _SECRET_VALUE,
            purpose="application-build-test",
            allowed_consumers=(_SECRET_CONSUMER,),
            allowed_purposes=(_SECRET_PURPOSE,),
        )
        await secrets.revoke(reference)
        lifecycle = _lifecycle(tmp_path, secret_provider=secrets)

        with pytest.raises(ContractError) as raised:
            await lifecycle._build_environment(  # noqa: SLF001 - exact boundary regression test
                _release(project_id, reference),
                task_id=new_id("task"),
                run_id=new_id("run"),
                timeout_seconds=None,
            )

        assert raised.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_unauthorized_secret_consumer_and_purpose_fail_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        reference = _reference(project_id)
        secrets = LocalSecretProvider()
        await secrets.create(
            reference,
            _SECRET_VALUE,
            purpose="application-build-test",
            allowed_consumers=("service:allowed-build-consumer",),
            allowed_purposes=("allowed-build-purpose",),
        )

        denied_consumer = _lifecycle(tmp_path, secret_provider=secrets)
        with pytest.raises(ContractError) as consumer_error:
            await denied_consumer._build_environment(  # noqa: SLF001
                _release(project_id, reference),
                task_id=new_id("task"),
                run_id=new_id("run"),
                timeout_seconds=None,
            )
        assert consumer_error.value.code is ErrorCode.FORBIDDEN

        denied_purpose = _lifecycle(
            tmp_path,
            secret_provider=secrets,
            consumer_ref="service:allowed-build-consumer",
            purpose=_SECRET_PURPOSE,
        )
        with pytest.raises(ContractError) as purpose_error:
            await denied_purpose._build_environment(  # noqa: SLF001
                _release(project_id, reference),
                task_id=new_id("task"),
                run_id=new_id("run"),
                timeout_seconds=None,
            )
        assert purpose_error.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_release_manifest_contains_neither_secret_reference_nor_material() -> None:
    project_id = new_id("project")
    reference = _reference(project_id)
    release = _release(project_id, reference)

    serialized = json.dumps(release_manifest(release), sort_keys=True)

    assert reference.secret_id not in serialized
    assert _SECRET_VALUE not in serialized
    assert "PRIVATE_INDEX_TOKEN" not in serialized
    assert release.build_specification.spec_id in serialized


def test_successful_build_replay_does_not_resolve_secret_again(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id="application-build-secret-replay",
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
        secrets = _CountingSecretProvider()
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
            application_id="secret-replay-app",
            display_name="Secret Replay App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision="source-revision-748-replay",
            build_specification=BuildSpecification(
                command=(
                    sys.executable,
                    "-c",
                    (
                        "import os; from pathlib import Path; "
                        "assert os.environ.get('PRIVATE_INDEX_TOKEN'); "
                        "Path('dist').mkdir(); Path('dist/app.bin').write_bytes(b'replay')"
                    ),
                ),
                targets=(_target(),),
                secret_environment={"PRIVATE_INDEX_TOKEN": reference},
            ),
            creator_ref="user:tester",
        )

        first = await service.request_build(
            release.release_id,
            target_id="local-test",
            idempotency_key="secret-replay-build",
            actor_ref="user:tester",
        )
        replay = await service.request_build(
            release.release_id,
            target_id="local-test",
            idempotency_key="secret-replay-build",
            actor_ref="user:tester",
        )

        assert first.status is ReleaseStatus.READY
        assert replay == first
        assert len(secrets.contexts) == 1
        assert _SECRET_VALUE not in repr(first)

    asyncio.run(scenario())