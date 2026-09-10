from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.application_distribution import (
    ApplicationDistributionService,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    BuildTargetStatus,
    GateEvidence,
    GateStatus,
    InMemoryApplicationReleaseRepository,
    JsonApplicationReleaseRepository,
    PackageType,
    PublicationResult,
    PublishContext,
    PublishedArtifact,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
    canonical_manifest_bytes,
    manifest_sha256,
    release_manifest,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.workspaces import InMemoryRunWorkspaceBindingRepository


@dataclass
class _Task:
    task_id: str
    plan_ref: str | None = None


class _Kernel:
    def __init__(self) -> None:
        self.tasks_by_key: dict[str, _Task] = {}
        self.tasks_by_id: dict[str, _Task] = {}
        self.runs: dict[tuple[str, str], object] = {}

    async def create_task(self, **kwargs: object) -> _Task:
        key = str(kwargs["idempotency_key"])
        existing = self.tasks_by_key.get(key)
        if existing is not None:
            return existing
        task = _Task(str(kwargs["task_id"]))
        self.tasks_by_key[key] = task
        self.tasks_by_id[task.task_id] = task
        return task

    async def ready_task(self, **kwargs: object) -> _Task:
        return self.tasks_by_id[str(kwargs["task_id"])]

    async def get_task(self, task_id: str) -> _Task:
        return self.tasks_by_id[task_id]

    async def plan_task(self, **kwargs: object) -> _Task:
        task = self.tasks_by_id[str(kwargs["task_id"])]
        planned = replace(task, plan_ref=f"plan:{task.task_id}")
        self.tasks_by_id[task.task_id] = planned
        for key, candidate in tuple(self.tasks_by_key.items()):
            if candidate.task_id == task.task_id:
                self.tasks_by_key[key] = planned
        return planned

    async def create_run(self, **kwargs: object) -> object:
        task_id = str(kwargs["task_id"])
        run_id = new_id("run")
        run = SimpleNamespace(
            run_id=run_id,
            status=RunStatus.QUEUED,
            artifact_ids=(),
            output={},
        )
        self.runs[(task_id, run_id)] = run
        return run

    async def start_run(self, **kwargs: object) -> object:
        task_id = str(kwargs["task_id"])
        run_id = str(kwargs["run_id"])
        run = SimpleNamespace(
            run_id=run_id,
            status=RunStatus.RUNNING,
            artifact_ids=(),
            output={},
        )
        self.runs[(task_id, run_id)] = run
        return run

    async def refresh_run(self, **kwargs: object) -> object:
        return self.runs[(str(kwargs["task_id"]), str(kwargs["run_id"]))]

    async def get_run(self, task_id: str, run_id: str) -> object:
        return self.runs[(task_id, run_id)]


class _Files:
    def __init__(self, file_id: str, artifact_id: str, sha256: str) -> None:
        self.record = SimpleNamespace(
            file_id=file_id,
            artifact_ids=(artifact_id,),
            sha256=sha256,
        )
        self.valid = True

    async def get_file(self, file_id: str, context: DataAccessContext) -> object:
        del context
        assert file_id == self.record.file_id
        return self.record

    async def verify_checksum(self, file_id: str, context: DataAccessContext) -> bool:
        del context
        assert file_id == self.record.file_id
        return self.valid


class _Workspaces:
    def __init__(self, project_id: str, source_revision: str) -> None:
        workspace_id = new_id("workspace")
        snapshot_id = new_id("workspace_snapshot")
        self.workspace = SimpleNamespace(
            id=workspace_id,
            project_id=project_id,
            base_snapshot_id=snapshot_id,
        )
        self.snapshot = SimpleNamespace(
            id=snapshot_id,
            workspace_id=workspace_id,
            content_checksum="c" * 64,
            source_revision=source_revision,
        )

    async def get_workspace(self, workspace_id: str) -> object:
        assert workspace_id == self.workspace.id
        return self.workspace

    async def get_snapshot(self, snapshot_id: str) -> object:
        assert snapshot_id == self.snapshot.id
        return self.snapshot


class _Publisher:
    provider_id = "reference-downloads"

    def __init__(self) -> None:
        self.publish_calls = 0

    async def preview(
        self,
        release: ApplicationRelease,
        manifest: dict[str, object],
        context: object,
    ) -> dict[str, object]:
        del release, context
        encoded = json.dumps(manifest, sort_keys=True).encode()
        return {"manifest_size": len(encoded)}

    async def publish(
        self,
        release: ApplicationRelease,
        manifest: dict[str, object],
        context: object,
    ) -> PublicationResult:
        del manifest, context
        self.publish_calls += 1
        return PublicationResult(
            provider_id=self.provider_id,
            release_url="https://downloads.example/app/v1.2.3",
            latest_url="https://downloads.example/app/latest",
            visibility=ReleaseVisibility.PUBLIC,
            artifacts=tuple(
                PublishedArtifact(
                    artifact_id=item.artifact_id,
                    download_url=(f"https://downloads.example/app/v1.2.3/{item.filename}"),
                )
                for item in release.artifacts
            ),
        )


class _RejectMatcher:
    async def supports(self, specification: object, target: object) -> bool:
        del specification, target
        return False


def _spec() -> BuildSpecification:
    return BuildSpecification(
        command=("python", "-m", "build"),
        targets=(
            BuildTarget(
                target_id="linux-x64",
                os_name="linux",
                architecture="x86_64",
                package_type=PackageType.ARCHIVE,
                output_path="dist/app-linux-x64.tar.gz",
                required_capabilities=("os:linux", "arch:x86_64"),
            ),
        ),
        spec_id="build_spec_00000000-0000-0000-0000-000000000001",
        test_gates=("tests", "package-smoke"),
        required_capabilities=("python",),
        secret_references=("secret_ref_signing_key",),
    )


def _authorization_gate(project_id: str) -> AuthorizationGate:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:tester",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.EXECUTE}),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project_id}),
            ),
        )
    )
    return AuthorizationGate(provider)


def _publish_context(project_id: str) -> PublishContext:
    return PublishContext(
        actor=ActorIdentity("user:tester", ActorType.HUMAN),
        operation=OperationContext(
            correlation_id="release-publish",
            owner_type="user",
            owner_id="tester",
            project_id=project_id,
        ),
    )


async def _create_release(
    service: ApplicationDistributionService,
    workspaces: _Workspaces,
    project_id: str,
    source_revision: str,
) -> ApplicationRelease:
    return await service.create_release(
        application_id="example-app",
        display_name="Example App",
        version="1.2.3",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PUBLIC,
        project_id=project_id,
        workspace_id=workspaces.workspace.id,
        workspace_snapshot_id=workspaces.snapshot.id,
        source_revision=source_revision,
        build_specification=_spec(),
        creator_ref="user:tester",
    )


def test_manifest_is_deterministic_schema_valid_and_preserves_snapshot() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        source_revision = "0123456789abcdef0123456789abcdef01234567"
        workspaces = _Workspaces(project_id, source_revision)
        kernel = _Kernel()
        artifact_id = new_id("artifact")
        file_id = new_id("file")
        service = ApplicationDistributionService(
            InMemoryApplicationReleaseRepository(),
            kernel=kernel,  # type: ignore[arg-type]
            files=_Files(file_id, artifact_id, "a" * 64),  # type: ignore[arg-type]
            workspaces=workspaces,  # type: ignore[arg-type]
            run_workspace_bindings=InMemoryRunWorkspaceBindingRepository(),
        )
        release = await _create_release(
            service,
            workspaces,
            project_id,
            source_revision,
        )
        first = canonical_manifest_bytes(release)
        second = canonical_manifest_bytes(release)
        assert first == second
        assert manifest_sha256(release) == manifest_sha256(release)
        assert b'"target":"linux-x64"' in first
        assert release.workspace_snapshot_id.encode() in first
        assert release.workspace_content_checksum.encode() in first
        schema_path = (
            Path(__file__).parents[1]
            / "docs"
            / "schemas"
            / "application-release-manifest.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(release_manifest(release))

    asyncio.run(scenario())


def test_release_build_binds_exact_snapshot_and_publish_is_policy_gated() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        source_revision = "fedcba9876543210fedcba9876543210fedcba98"
        workspaces = _Workspaces(project_id, source_revision)
        kernel = _Kernel()
        artifact_id = new_id("artifact")
        file_id = new_id("file")
        files = _Files(file_id, artifact_id, "b" * 64)
        bindings = InMemoryRunWorkspaceBindingRepository()
        publisher = _Publisher()
        service = ApplicationDistributionService(
            InMemoryApplicationReleaseRepository(),
            kernel=kernel,  # type: ignore[arg-type]
            files=files,  # type: ignore[arg-type]
            workspaces=workspaces,  # type: ignore[arg-type]
            run_workspace_bindings=bindings,
            authorization_gate=_authorization_gate(project_id),
            publishers=(publisher,),  # type: ignore[arg-type]
        )
        release = await _create_release(
            service,
            workspaces,
            project_id,
            source_revision,
        )
        duplicate = await _create_release(
            service,
            workspaces,
            project_id,
            source_revision,
        )
        assert duplicate.release_id == release.release_id

        release = await service.request_build(
            release.release_id,
            target_id="linux-x64",
            idempotency_key="build-1",
            actor_ref="user:tester",
        )
        retry = await service.request_build(
            release.release_id,
            target_id="linux-x64",
            idempotency_key="build-1",
            actor_ref="user:tester",
        )
        assert retry.targets[0].task_id == release.targets[0].task_id
        assert retry.targets[0].run_id == release.targets[0].run_id
        assert release.targets[0].status is BuildTargetStatus.RUNNING
        task_id = release.targets[0].task_id
        run_id = release.targets[0].run_id
        assert task_id is not None
        assert run_id is not None
        binding = await bindings.get(run_id)
        assert binding is not None
        assert binding.workspace_id == release.workspace_id
        assert binding.workspace_snapshot_id == release.workspace_snapshot_id
        assert binding.content_checksum == release.workspace_content_checksum

        kernel.runs[(task_id, run_id)] = SimpleNamespace(
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
            artifact_ids=(artifact_id,),
            output={},
        )
        context = DataAccessContext(
            operation=OperationContext(
                correlation_id=release.release_id,
                owner_type="user",
                owner_id="tester",
                project_id=release.project_id,
            ),
            actor_ref="user:tester",
            task_id=task_id,
            run_id=run_id,
        )
        release = await service.record_build_artifact(
            release.release_id,
            target_id="linux-x64",
            artifact_id=artifact_id,
            file_id=file_id,
            filename="example-app-linux-x64.tar.gz",
            media_type="application/gzip",
            build_run_id=run_id,
            context=context,
            evidence_refs=("verification_package",),
        )
        assert release.status is ReleaseStatus.READY
        for name in ("tests", "package-smoke"):
            release = await service.record_gate(
                release.release_id,
                GateEvidence(
                    name=name,
                    status=GateStatus.PASSED,
                    evidence_refs=(f"evidence_{name}",),
                ),
            )
        publish_context = _publish_context(project_id)
        preview = await service.preview_publication(
            release.release_id,
            publisher_id=publisher.provider_id,
            context=publish_context,
        )
        assert preview["visibility"] == "public"
        assert preview["manifest_sha256"] == manifest_sha256(release)
        published = await service.publish(
            release.release_id,
            publisher_id=publisher.provider_id,
            context=publish_context,
        )
        assert published.status is ReleaseStatus.PUBLISHED
        assert published.release_url == "https://downloads.example/app/v1.2.3"
        assert published.artifacts[0].download_url is not None
        assert publisher.publish_calls == 1
        republished = await service.publish(
            release.release_id,
            publisher_id=publisher.provider_id,
            context=publish_context,
        )
        assert republished == published
        assert publisher.publish_calls == 1

    asyncio.run(scenario())


def test_mismatched_run_workspace_binding_blocks_artifact_admission() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        source_revision = "1" * 40
        workspaces = _Workspaces(project_id, source_revision)
        kernel = _Kernel()
        artifact_id = new_id("artifact")
        file_id = new_id("file")
        bindings = InMemoryRunWorkspaceBindingRepository()
        service = ApplicationDistributionService(
            InMemoryApplicationReleaseRepository(),
            kernel=kernel,  # type: ignore[arg-type]
            files=_Files(file_id, artifact_id, "d" * 64),  # type: ignore[arg-type]
            workspaces=workspaces,  # type: ignore[arg-type]
            run_workspace_bindings=bindings,
        )
        release = await _create_release(
            service,
            workspaces,
            project_id,
            source_revision,
        )
        release = await service.request_build(
            release.release_id,
            target_id="linux-x64",
            idempotency_key="binding-mismatch",
            actor_ref="user:tester",
        )
        task_id = release.targets[0].task_id
        run_id = release.targets[0].run_id
        assert task_id is not None
        assert run_id is not None
        kernel.runs[(task_id, run_id)] = SimpleNamespace(
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
            artifact_ids=(artifact_id,),
            output={},
        )
        binding = await bindings.get(run_id)
        assert binding is not None
        bindings._bindings[run_id] = replace(  # noqa: SLF001 - deliberate tamper fixture
            binding,
            workspace_snapshot_id=new_id("workspace_snapshot"),
        )
        context = DataAccessContext(
            operation=OperationContext(
                correlation_id=release.release_id,
                project_id=project_id,
            ),
            actor_ref="user:tester",
            task_id=task_id,
            run_id=run_id,
        )
        with pytest.raises(ContractError) as raised:
            await service.record_build_artifact(
                release.release_id,
                target_id="linux-x64",
                artifact_id=artifact_id,
                file_id=file_id,
                filename="example-app-linux-x64.tar.gz",
                media_type="application/gzip",
                build_run_id=run_id,
                context=context,
            )
        assert raised.value.code is ErrorCode.CONTRACT_VIOLATION

    asyncio.run(scenario())


def test_missing_target_host_is_explicitly_unsupported() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        source_revision = "2" * 40
        workspaces = _Workspaces(project_id, source_revision)
        service = ApplicationDistributionService(
            InMemoryApplicationReleaseRepository(),
            kernel=_Kernel(),  # type: ignore[arg-type]
            files=_Files(new_id("file"), new_id("artifact"), "e" * 64),  # type: ignore[arg-type]
            workspaces=workspaces,  # type: ignore[arg-type]
            run_workspace_bindings=InMemoryRunWorkspaceBindingRepository(),
            target_matcher=_RejectMatcher(),  # type: ignore[arg-type]
        )
        release = await _create_release(
            service,
            workspaces,
            project_id,
            source_revision,
        )
        release = await service.request_build(
            release.release_id,
            target_id="linux-x64",
            idempotency_key="unsupported",
            actor_ref="user:tester",
        )
        assert release.status is ReleaseStatus.FAILED
        assert release.targets[0].status is BuildTargetStatus.UNSUPPORTED
        assert release.targets[0].task_id is None
        assert release.targets[0].failure_reason is not None

    asyncio.run(scenario())


def test_publish_fails_closed_without_authorization_gate() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        source_revision = "3" * 40
        workspaces = _Workspaces(project_id, source_revision)
        publisher = _Publisher()
        service = ApplicationDistributionService(
            InMemoryApplicationReleaseRepository(),
            kernel=_Kernel(),  # type: ignore[arg-type]
            files=_Files(new_id("file"), new_id("artifact"), "f" * 64),  # type: ignore[arg-type]
            workspaces=workspaces,  # type: ignore[arg-type]
            run_workspace_bindings=InMemoryRunWorkspaceBindingRepository(),
            publishers=(publisher,),  # type: ignore[arg-type]
        )
        release = await _create_release(
            service,
            workspaces,
            project_id,
            source_revision,
        )
        release = replace(
            release,
            status=ReleaseStatus.READY,
            artifacts=(),
            targets=(),
            build_specification=BuildSpecification(
                command=("true",),
                targets=_spec().targets,
            ),
        )
        service.repository._items[release.release_id] = release  # type: ignore[attr-defined]
        with pytest.raises(ContractError) as raised:
            await service.publish(
                release.release_id,
                publisher_id=publisher.provider_id,
                context=_publish_context(project_id),
            )
        assert raised.value.code is ErrorCode.UNAVAILABLE
        assert publisher.publish_calls == 0

    asyncio.run(scenario())


def test_json_release_repository_restores_stable_release_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        source_revision = "4" * 40
        workspaces = _Workspaces(project_id, source_revision)
        path = tmp_path / "application-releases.json"
        repository = JsonApplicationReleaseRepository(path)
        service = ApplicationDistributionService(
            repository,
            kernel=_Kernel(),  # type: ignore[arg-type]
            files=_Files(new_id("file"), new_id("artifact"), "a" * 64),  # type: ignore[arg-type]
            workspaces=workspaces,  # type: ignore[arg-type]
            run_workspace_bindings=InMemoryRunWorkspaceBindingRepository(),
        )
        release = await _create_release(
            service,
            workspaces,
            project_id,
            source_revision,
        )
        restored_repository = JsonApplicationReleaseRepository(path)
        restored = await restored_repository.get(release.release_id)
        assert restored.release_id == release.release_id
        assert restored.workspace_snapshot_id == release.workspace_snapshot_id
        assert restored.workspace_content_checksum == release.workspace_content_checksum
        assert restored.build_specification == release.build_specification

    asyncio.run(scenario())
