from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.application_distribution import (
    ApplicationDistributionService,
    InMemoryApplicationReleaseRepository,
    PublicationResult,
    PublishedArtifact,
    ReleaseStatus,
)
from ai_multi_agent_platform.application_distribution.control_plane import (
    APPLICATION_RELEASE_COLLECTION,
    register_application_distribution_control_plane,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    JsonValue,
    OperationContext,
)
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)
from ai_multi_agent_platform.workspaces import InMemoryRunWorkspaceBindingRepository

_SOURCE = "7" * 40
_ARTIFACT_SHA = "8" * 64
_SAFE_CONNECTION = "connection_issue751"
_SAFE_REPOSITORY = "ScoreSymphony/example-app"


class _PublicationPolicy(FakeAuthorizationProvider):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    async def authorize(self, request):
        self.calls.append(request)
        if request.action == AuthorizationAction.APPROVE.value:
            return AuthorizationDecision(
                AuthorizationOutcome.ALLOW,
                reason="issue751 approver",
            )
        if request.side_effect != "application_release_publish":
            return AuthorizationDecision(
                AuthorizationOutcome.ALLOW,
                reason="issue751 build allowed",
            )
        if self.mode == "deny":
            return AuthorizationDecision(
                AuthorizationOutcome.DENY,
                reason="issue751 publication denied",
                policy_id="issue751-publish-policy",
            )
        if self.mode == "approval":
            return AuthorizationDecision(
                AuthorizationOutcome.REQUIRE_APPROVAL,
                reason="issue751 publication review required",
                policy_id="issue751-publish-policy",
            )
        return AuthorizationDecision(
            AuthorizationOutcome.ALLOW,
            reason="issue751 publication allowed",
        )


@dataclass(slots=True)
class _Task:
    task_id: str


class _BuildKernel:
    def __init__(self, *, artifact_id: str, file_id: str) -> None:
        self.artifact_id = artifact_id
        self.file_id = file_id
        self.release_id: str | None = None
        self.tasks: dict[str, _Task] = {}
        self.runs: dict[tuple[str, str], object] = {}

    async def create_task(self, **kwargs: object) -> _Task:
        task_id = str(kwargs["task_id"])
        task = _Task(task_id)
        self.tasks[task_id] = task
        return task

    async def ready_task(self, **kwargs: object) -> _Task:
        return self.tasks[str(kwargs["task_id"])]

    async def create_run(self, **kwargs: object) -> object:
        task_id = str(kwargs["task_id"])
        run = SimpleNamespace(
            run_id=new_id("run"),
            status=RunStatus.QUEUED,
            artifact_ids=(),
            output={},
        )
        self.runs[(task_id, run.run_id)] = run
        return run

    async def start_run(self, **kwargs: object) -> object:
        key = (str(kwargs["task_id"]), str(kwargs["run_id"]))
        current = self.runs[key]
        running = SimpleNamespace(
            run_id=current.run_id,
            status=RunStatus.RUNNING,
            artifact_ids=current.artifact_ids,
            output=current.output,
        )
        self.runs[key] = running
        return running

    async def refresh_run(self, **kwargs: object) -> object:
        key = (str(kwargs["task_id"]), str(kwargs["run_id"]))
        if self.release_id is None:
            raise AssertionError("fixture release id must be assigned before build refresh")
        succeeded = SimpleNamespace(
            run_id=key[1],
            status=RunStatus.SUCCEEDED,
            artifact_ids=(),
            output={
                "output": {
                    "application_build": {
                        "release_id": self.release_id,
                        "target_id": "linux-x64",
                        "artifact_id": self.artifact_id,
                        "file_id": self.file_id,
                        "filename": "example-app-linux-x64.tar.gz",
                        "media_type": "application/gzip",
                        "sha256": _ARTIFACT_SHA,
                        "runtime_provenance": {
                            "worker_id": "worker_issue751_linux",
                            "node_id": "node_issue751_remote",
                            "executor_id": "executor_issue751",
                            "executor_version": "1.0",
                            "os": "linux",
                            "architecture": "x86_64",
                        },
                    }
                }
            },
        )
        self.runs[key] = succeeded
        return succeeded

    async def get_run(self, task_id: str, run_id: str) -> object:
        return self.runs[(task_id, run_id)]

    async def attach_artifact(self, **kwargs: object) -> object:
        key = (str(kwargs["task_id"]), str(kwargs["run_id"]))
        artifact_id = str(kwargs["artifact_id"])
        current = self.runs[key]
        updated = SimpleNamespace(
            run_id=current.run_id,
            status=current.status,
            artifact_ids=tuple(dict.fromkeys((*current.artifact_ids, artifact_id))),
            output=current.output,
        )
        self.runs[key] = updated
        return updated


class _Files:
    def __init__(self, *, file_id: str, artifact_id: str) -> None:
        self.record = SimpleNamespace(
            file_id=file_id,
            artifact_ids=(artifact_id,),
            sha256=_ARTIFACT_SHA,
        )

    async def get_file(self, file_id: str, context: DataAccessContext) -> object:
        del context
        assert file_id == self.record.file_id
        return self.record

    async def verify_checksum(self, file_id: str, context: DataAccessContext) -> bool:
        del context
        assert file_id == self.record.file_id
        return True


class _Workspaces:
    def __init__(self, project_id: str) -> None:
        self.workspace = SimpleNamespace(
            id=new_id("workspace"),
            project_id=project_id,
            base_snapshot_id=new_id("workspace_snapshot"),
        )
        self.snapshot = SimpleNamespace(
            id=self.workspace.base_snapshot_id,
            workspace_id=self.workspace.id,
            content_checksum="9" * 64,
            source_revision=_SOURCE,
        )

    async def get_workspace(self, workspace_id: str) -> object:
        assert workspace_id == self.workspace.id
        return self.workspace

    async def get_snapshot(self, snapshot_id: str) -> object:
        assert snapshot_id == self.snapshot.id
        return self.snapshot


class _Publisher:
    provider_id = "issue751-reference-publisher"

    def __init__(self) -> None:
        self.preview_calls = 0
        self.publish_calls = 0
        self.fail_publish = False

    async def preview(self, release, manifest, context):
        del manifest
        self.preview_calls += 1
        return {
            "provider": self.provider_id,
            "release_id": release.release_id,
            "connection_id": context.configuration.get("connection_id"),
            "repository_ref": context.configuration.get("repository_ref"),
            "side_effects": [],
        }

    async def publish(self, release, manifest, context):
        del manifest, context
        self.publish_calls += 1
        if self.fail_publish:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "issue751 reference publisher unavailable",
                retryable=True,
            )
        return PublicationResult(
            provider_id=self.provider_id,
            release_url="https://downloads.example/releases/v1.0.0",
            latest_url="https://downloads.example/releases/latest",
            visibility=release.visibility,
            artifacts=tuple(
                PublishedArtifact(
                    artifact_id=artifact.artifact_id,
                    download_url=(
                        "https://downloads.example/releases/v1.0.0/"
                        f"{artifact.filename}"
                    ),
                    external_metadata={
                        "reference": {"asset_id": f"asset:{artifact.artifact_id}"}
                    },
                )
                for artifact in release.artifacts
            ),
            external_metadata={"reference": {"release_id": "release:issue751"}},
        )


@dataclass(slots=True)
class _Harness:
    control_plane: ControlPlane
    service: ApplicationDistributionService
    build_kernel: _BuildKernel
    publisher: _Publisher
    policy: _PublicationPolicy
    project_id: str
    workspaces: _Workspaces


def _context(key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id="issue751-control-plane",
        actor=ActorContext(
            principal_ref="user:tester",
            owner_type="user",
            owner_id="tester",
            actor_type="human",
        ),
        idempotency_key=key,
    )


def _read_context() -> RequestContext:
    return RequestContext(
        request_id="request-issue751-read",
        correlation_id="issue751-control-plane",
        actor=ActorContext(
            principal_ref="user:tester",
            owner_type="user",
            owner_id="tester",
            actor_type="human",
        ),
    )


def _create_payload(harness: _Harness) -> dict[str, JsonValue]:
    return {
        "application_id": "example-app",
        "display_name": "Example App",
        "version": "1.0.0",
        "channel": "stable",
        "visibility": "public",
        "project_id": harness.project_id,
        "workspace_id": harness.workspaces.workspace.id,
        "workspace_snapshot_id": harness.workspaces.snapshot.id,
        "source_revision": _SOURCE,
        "release_notes": "Issue 751 acceptance fixture",
        "build_specification": {
            "command": ["python", "-m", "build"],
            "targets": [
                {
                    "target_id": "linux-x64",
                    "os_name": "linux",
                    "architecture": "x86_64",
                    "package_type": "archive",
                    "output_path": "dist/example-app-linux-x64.tar.gz",
                    "required_capabilities": ["os:linux", "arch:x86_64"],
                }
            ],
            "required_capabilities": ["python"],
            "secret_environment": {
                "SIGNING_TOKEN": {
                    "provider": "local-secrets",
                    "secret_id": "issue751-signing-token",
                    "scope": harness.project_id,
                }
            },
        },
    }


def _publisher_configuration(
    repository_ref: str = _SAFE_REPOSITORY,
) -> dict[str, JsonValue]:
    return {
        "connection_id": _SAFE_CONNECTION,
        "repository_ref": repository_ref,
    }


def _harness(mode: str) -> _Harness:
    project_id = new_id("project")
    artifact_id = new_id("artifact")
    file_id = new_id("file")
    workspaces = _Workspaces(project_id)
    build_kernel = _BuildKernel(artifact_id=artifact_id, file_id=file_id)
    policy = _PublicationPolicy(mode)
    publisher = _Publisher()
    service = ApplicationDistributionService(
        InMemoryApplicationReleaseRepository(),
        kernel=build_kernel,  # type: ignore[arg-type]
        files=_Files(file_id=file_id, artifact_id=artifact_id),  # type: ignore[arg-type]
        workspaces=workspaces,  # type: ignore[arg-type]
        run_workspace_bindings=InMemoryRunWorkspaceBindingRepository(),
        authorization_gate=AuthorizationGate(policy),
        publishers=(publisher,),  # type: ignore[arg-type]
    )

    events = InMemoryKernelRepository()
    control_plane = ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        ),
        events=events,
        authorization=FakeAuthorizationProvider(allowed=True),
    )
    register_application_distribution_control_plane(control_plane, service)
    return _Harness(
        control_plane=control_plane,
        service=service,
        build_kernel=build_kernel,
        publisher=publisher,
        policy=policy,
        project_id=project_id,
        workspaces=workspaces,
    )


async def _create_and_build(
    harness: _Harness,
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    created = await harness.control_plane.execute_command(
        _context("issue751-create"),
        "application-release.create",
        APPLICATION_RELEASE_COLLECTION,
        _create_payload(harness),
    )
    harness.build_kernel.release_id = str(created["id"])
    built = await harness.control_plane.execute_command(
        _context("issue751-build"),
        "application-release.build",
        str(created["id"]),
        {"target_id": "linux-x64"},
    )
    return created, built


def test_versioned_control_plane_full_release_flow_preserves_canonical_and_download_metadata(
) -> None:
    async def scenario() -> None:
        harness = _harness("allow")
        created, built = await _create_and_build(harness)
        release_id = str(created["id"])

        replay = await harness.control_plane.execute_command(
            _context("issue751-create"),
            "application-release.create",
            APPLICATION_RELEASE_COLLECTION,
            _create_payload(harness),
        )
        assert replay["id"] == release_id
        assert built["id"] == release_id
        assert built["status"] == ReleaseStatus.READY.value
        assert built["source_revision"] == _SOURCE
        assert built["workspace_snapshot_id"] == harness.workspaces.snapshot.id

        targets = built["targets"]
        artifacts = built["artifacts"]
        assert isinstance(targets, list) and len(targets) == 1
        assert isinstance(artifacts, list) and len(artifacts) == 1
        target = targets[0]
        artifact = artifacts[0]
        assert isinstance(target, dict)
        assert isinstance(artifact, dict)
        target_definition = target["target"]
        assert isinstance(target_definition, dict)
        assert target_definition["os_name"] == "linux"
        assert target_definition["architecture"] == "x86_64"
        assert target_definition["package_type"] == "archive"
        assert artifact["sha256"] == _ARTIFACT_SHA
        artifact_metadata = artifact["external_metadata"]
        assert isinstance(artifact_metadata, dict)
        assert artifact_metadata["worker_id"] == "worker_issue751_linux"
        assert artifact_metadata["node_id"] == "node_issue751_remote"

        listed = await harness.control_plane.list_extension_resources(
            _read_context(),
            APPLICATION_RELEASE_COLLECTION,
            PageQuery(),
        )
        assert listed["total"] == 1
        listed_items = listed["items"]
        assert isinstance(listed_items, list)
        assert listed_items[0]["id"] == release_id
        shown = await harness.control_plane.get_extension_resource(
            _read_context(),
            APPLICATION_RELEASE_COLLECTION,
            release_id,
        )
        assert shown["id"] == release_id
        assert shown["status"] == ReleaseStatus.READY.value

        preview = await harness.control_plane.execute_command(
            _context("issue751-preview"),
            "application-release.preview",
            release_id,
            {
                "publisher_id": harness.publisher.provider_id,
                "publisher_configuration": _publisher_configuration(),
            },
        )
        assert preview["id"] == release_id
        assert preview["release_id"] == release_id
        assert preview["provider_id"] == harness.publisher.provider_id
        assert harness.publisher.preview_calls == 1
        assert harness.publisher.publish_calls == 0

        published = await harness.control_plane.execute_command(
            _context("issue751-publish"),
            "application-release.publish",
            release_id,
            {
                "publisher_id": harness.publisher.provider_id,
                "publisher_configuration": _publisher_configuration(),
            },
        )
        assert published["id"] == release_id
        assert published["status"] == ReleaseStatus.PUBLISHED.value
        assert published["release_url"] == "https://downloads.example/releases/v1.0.0"
        assert published["latest_url"] == "https://downloads.example/releases/latest"
        published_artifacts = published["artifacts"]
        assert isinstance(published_artifacts, list) and len(published_artifacts) == 1
        published_artifact = published_artifacts[0]
        assert isinstance(published_artifact, dict)
        assert published_artifact["target_id"] == "linux-x64"
        assert published_artifact["sha256"] == _ARTIFACT_SHA
        assert published_artifact["download_url"] == (
            "https://downloads.example/releases/v1.0.0/example-app-linux-x64.tar.gz"
        )
        published_metadata = published_artifact["external_metadata"]
        assert isinstance(published_metadata, dict)
        assert published_metadata["worker_id"] == "worker_issue751_linux"
        assert published_metadata["node_id"] == "node_issue751_remote"
        reference_metadata = published_metadata["reference"]
        assert isinstance(reference_metadata, dict)
        assert str(reference_metadata["asset_id"]).startswith("asset:artifact_")
        assert published["external_metadata"] == {
            "reference": {"release_id": "release:issue751"}
        }
        assert harness.publisher.publish_calls == 1

        final_show = await harness.control_plane.get_extension_resource(
            _read_context(),
            APPLICATION_RELEASE_COLLECTION,
            release_id,
        )
        assert final_show == published
        serialized = json.dumps(final_show, sort_keys=True)
        assert "issue751-signing-token" in serialized
        assert "secret_environment" in serialized
        assert "Bearer " not in serialized

    asyncio.run(scenario())


def test_actor_may_build_but_publication_policy_denies_publish() -> None:
    async def scenario() -> None:
        harness = _harness("deny")
        _created, built = await _create_and_build(harness)
        release_id = str(built["id"])
        assert built["status"] == ReleaseStatus.READY.value
        assert any(
            call.side_effect == "application_build_execute" for call in harness.policy.calls
        )

        with pytest.raises(ContractError) as caught:
            await harness.control_plane.execute_command(
                _context("issue751-denied-publish"),
                "application-release.publish",
                release_id,
                {
                    "publisher_id": harness.publisher.provider_id,
                    "publisher_configuration": _publisher_configuration(),
                },
            )
        assert caught.value.code is ErrorCode.FORBIDDEN
        assert harness.publisher.publish_calls == 0
        current = await harness.service.repository.get(release_id)
        assert current.status is ReleaseStatus.READY
        assert current.release_url is None

    asyncio.run(scenario())


def test_publication_approval_is_exact_and_preview_is_side_effect_free() -> None:
    async def scenario() -> None:
        harness = _harness("approval")
        _created, built = await _create_and_build(harness)
        release_id = str(built["id"])

        preview = await harness.control_plane.execute_command(
            _context("issue751-approval-preview"),
            "application-release.preview",
            release_id,
            {
                "publisher_id": harness.publisher.provider_id,
                "publisher_configuration": _publisher_configuration(),
            },
        )
        assert preview["release_id"] == release_id
        assert harness.publisher.preview_calls == 1
        assert harness.publisher.publish_calls == 0

        with pytest.raises(ContractError) as blocked:
            await harness.control_plane.execute_command(
                _context("issue751-approval-publish"),
                "application-release.publish",
                release_id,
                {
                    "publisher_id": harness.publisher.provider_id,
                    "publisher_configuration": _publisher_configuration(),
                },
            )
        assert blocked.value.code is ErrorCode.FORBIDDEN
        approval_id = blocked.value.details.get("approval_id")
        assert isinstance(approval_id, str)
        assert harness.publisher.publish_calls == 0

        gate = harness.service.authorization_gate
        assert gate is not None
        await gate.decide_approval(
            approval_id,
            approver=ActorIdentity("user:tester", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(correlation_id="issue751-approve-publication"),
        )

        with pytest.raises(ContractError) as changed:
            await harness.control_plane.execute_command(
                _context("issue751-changed-publication"),
                "application-release.publish",
                release_id,
                {
                    "publisher_id": harness.publisher.provider_id,
                    "approval_id": approval_id,
                    "publisher_configuration": _publisher_configuration(
                        "ScoreSymphony/different-app"
                    ),
                },
            )
        assert changed.value.code is ErrorCode.FORBIDDEN
        assert changed.value.details.get("approval_id") != approval_id
        assert harness.publisher.publish_calls == 0

        published = await harness.control_plane.execute_command(
            _context("issue751-approved-publication"),
            "application-release.publish",
            release_id,
            {
                "publisher_id": harness.publisher.provider_id,
                "approval_id": approval_id,
                "publisher_configuration": _publisher_configuration(),
            },
        )
        assert published["status"] == ReleaseStatus.PUBLISHED.value
        assert harness.publisher.publish_calls == 1

    asyncio.run(scenario())


def test_provider_failure_never_marks_canonical_release_published() -> None:
    async def scenario() -> None:
        harness = _harness("allow")
        _created, built = await _create_and_build(harness)
        release_id = str(built["id"])
        harness.publisher.fail_publish = True

        with pytest.raises(ContractError) as caught:
            await harness.control_plane.execute_command(
                _context("issue751-provider-failure"),
                "application-release.publish",
                release_id,
                {
                    "publisher_id": harness.publisher.provider_id,
                    "publisher_configuration": _publisher_configuration(),
                },
            )
        assert caught.value.code is ErrorCode.UNAVAILABLE
        current = await harness.service.repository.get(release_id)
        assert current.status is ReleaseStatus.READY
        assert current.publisher_id is None
        assert current.release_url is None
        assert current.latest_url is None
        assert all(artifact.download_url is None for artifact in current.artifacts)

    asyncio.run(scenario())
