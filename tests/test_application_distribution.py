from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from ai_multi_agent_platform.application_distribution import (
    ApplicationDistributionService,
    BuildSpecification,
    BuildTarget,
    GateEvidence,
    GateStatus,
    InMemoryApplicationReleaseRepository,
    PackageType,
    PublicationResult,
    PublishedArtifact,
    PublishContext,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
    canonical_manifest_bytes,
    manifest_sha256,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType


@dataclass
class _Task:
    task_id: str


class _Kernel:
    def __init__(self) -> None:
        self.tasks: dict[str, _Task] = {}
        self.runs: dict[tuple[str, str], object] = {}

    async def create_task(self, **kwargs: object) -> _Task:
        key = str(kwargs["idempotency_key"])
        existing = self.tasks.get(key)
        if existing is not None:
            return existing
        task = _Task(str(kwargs["task_id"]))
        self.tasks[key] = task
        return task

    async def ready_task(self, **kwargs: object) -> _Task:
        task_id = str(kwargs["task_id"])
        return _Task(task_id)

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


class _Publisher:
    provider_id = "reference-downloads"

    async def preview(self, release: object, manifest: dict[str, object], context: object) -> dict[str, object]:
        del release, context
        return {"manifest_sha256": __import__("hashlib").sha256(__import__("json").dumps(manifest, sort_keys=True).encode()).hexdigest()}

    async def publish(self, release: object, manifest: dict[str, object], context: object) -> PublicationResult:
        del manifest, context
        artifacts = getattr(release, "artifacts")
        return PublicationResult(
            provider_id=self.provider_id,
            release_url="https://downloads.example/app/v1.2.3",
            latest_url="https://downloads.example/app/latest",
            visibility=ReleaseVisibility.PUBLIC,
            artifacts=tuple(
                PublishedArtifact(
                    artifact_id=item.artifact_id,
                    download_url=f"https://downloads.example/app/v1.2.3/{item.filename}",
                )
                for item in artifacts
            ),
        )


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
        test_gates=("tests", "package-smoke"),
        required_capabilities=("python",),
        secret_references=("secret_ref_signing_key",),
    )


def test_manifest_is_deterministic_and_preserves_target_metadata() -> None:
    async def scenario() -> None:
        kernel = _Kernel()
        artifact_id = new_id("artifact")
        file_id = new_id("file")
        files = _Files(file_id, artifact_id, "a" * 64)
        repository = InMemoryApplicationReleaseRepository()
        service = ApplicationDistributionService(
            repository,
            kernel=kernel,  # type: ignore[arg-type]
            files=files,  # type: ignore[arg-type]
        )
        release = await service.create_release(
            application_id="example-app",
            display_name="Example App",
            version="1.2.3",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PUBLIC,
            project_id=new_id("project"),
            workspace_id=new_id("workspace"),
            source_revision="0123456789abcdef0123456789abcdef01234567",
            build_specification=_spec(),
            creator_ref="user:tester",
        )
        first = canonical_manifest_bytes(release)
        second = canonical_manifest_bytes(release)
        assert first == second
        assert manifest_sha256(release) == manifest_sha256(release)
        assert b'"target":"linux-x64"' in first
        assert b'"download_url":null' not in first

    asyncio.run(scenario())


def test_release_requires_canonical_run_file_integrity_and_gates_before_publish() -> None:
    async def scenario() -> None:
        kernel = _Kernel()
        artifact_id = new_id("artifact")
        file_id = new_id("file")
        files = _Files(file_id, artifact_id, "b" * 64)
        repository = InMemoryApplicationReleaseRepository()
        publisher = _Publisher()
        service = ApplicationDistributionService(
            repository,
            kernel=kernel,  # type: ignore[arg-type]
            files=files,  # type: ignore[arg-type]
            publishers=(publisher,),  # type: ignore[arg-type]
        )
        release = await service.create_release(
            application_id="example-app",
            display_name="Example App",
            version="1.2.3",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PUBLIC,
            project_id=new_id("project"),
            workspace_id=new_id("workspace"),
            source_revision="fedcba9876543210fedcba9876543210fedcba98",
            build_specification=_spec(),
            creator_ref="user:tester",
        )
        duplicate = await service.create_release(
            application_id="example-app",
            display_name="Example App",
            version="1.2.3",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PUBLIC,
            project_id=release.project_id,
            workspace_id=release.workspace_id,
            source_revision=release.source_revision,
            build_specification=release.build_specification,
            creator_ref="user:tester",
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
        task_id = release.targets[0].task_id
        assert task_id is not None
        run_id = new_id("run")
        kernel.runs[(task_id, run_id)] = SimpleNamespace(
            status=SimpleNamespace(value="succeeded"),
            artifact_ids=(artifact_id,),
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
        actor = ActorIdentity("user:tester", ActorType.HUMAN)
        publish_context = PublishContext(
            actor=actor,
            operation=OperationContext(
                correlation_id=release.release_id,
                owner_type="user",
                owner_id="tester",
                project_id=release.project_id,
            ),
            approval_id="approval_release",
        )
        for name in ("tests", "package-smoke"):
            release = await service.record_gate(
                release.release_id,
                GateEvidence(name=name, status=GateStatus.PASSED, evidence_refs=(f"evidence_{name}",)),
            )
        preview = await service.preview_publication(
            release.release_id,
            publisher_id=publisher.provider_id,
            context=publish_context,
        )
        assert preview["visibility"] == "public"
        published = await service.publish(
            release.release_id,
            publisher_id=publisher.provider_id,
            context=publish_context,
        )
        assert published.status is ReleaseStatus.PUBLISHED
        assert published.release_url == "https://downloads.example/app/v1.2.3"
        assert published.artifacts[0].download_url is not None
        republished = await service.publish(
            release.release_id,
            publisher_id=publisher.provider_id,
            context=publish_context,
        )
        assert republished == published

    asyncio.run(scenario())
