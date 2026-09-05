from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryConnection,
    RepositoryRegistry,
    RepositoryService,
)
from ai_multi_agent_platform.repositories.control_plane import register_repository_control_plane
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-repository-82",
        "X-Correlation-Id": "correlation-repository-82",
        "X-Principal-Ref": "user:repository-user",
        "X-Owner-Type": "user",
        "X-Owner-Id": "repository-user",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


async def _stack(tmp_path: Path) -> tuple[ControlPlane, ControlPlaneHTTP, Path, str]:
    project_id = new_id("project")
    connection = RepositoryConnection(
        connection=Connection(
            id=new_id("connection"),
            connector_type_id="local-git",
            connector_version="1.0",
            owner_type="user",
            owner_id="repository-user",
            display_name="Repository Control Plane fixture",
            project_id=project_id,
        ),
        provider_id="local-git",
        local=True,
    )
    root = tmp_path / "repo"
    provider = LocalGitRepositoryProvider(root, connection)
    operation = OperationContext(
        correlation_id="repository-control-plane-fixture",
        owner_type="user",
        owner_id="repository-user",
        project_id=project_id,
    )
    repository = await provider.initialize(operation)
    (root / "README.md").write_text("one\n", encoding="utf-8")
    await provider.commit(
        repository,
        "initial",
        operation,
        author_name="Repository Test",
        author_email="repository@example.invalid",
    )
    reference = await provider.read(repository, operation)

    registry = RepositoryRegistry()
    registry.register(RepositoryBinding(connection, reference, provider))
    authorization = AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:repository-user",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset(
                        {AuthorizationAction.READ, AuthorizationAction.MODIFY}
                    ),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
            )
        )
    )
    repositories = RepositoryService(registry, authorization)

    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    control_plane = ControlPlane(kernel=kernel, events=events)
    register_repository_control_plane(control_plane, repositories)
    return control_plane, ControlPlaneHTTP(control_plane), root, reference.id


def test_repository_control_plane_exposes_resources_and_policy_gated_git_commands(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        control_plane, http, root, repository_id = await _stack(tmp_path)
        assert "repositories" in control_plane.registered_collections
        assert "repository.status" in control_plane.registered_commands
        assert "repository.push" in control_plane.registered_commands

        listed = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/repositories", headers=_headers())
        )
        assert listed.status == 200
        assert isinstance(listed.body, dict)
        items = listed.body["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        assert isinstance(items[0], dict)
        assert items[0]["id"] == repository_id
        assert items[0]["external_resource"]["resource_type"] == "repository"

        loaded = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/repositories/{repository_id}",
                headers=_headers(),
            )
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["id"] == repository_id

        status = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/repository.status",
                headers=_headers("status-1"),
                body={"resource_ref": repository_id},
            )
        )
        assert status.status == 200
        assert isinstance(status.body, dict)
        assert status.body["clean"] is True

        (root / "README.md").write_text("two\n", encoding="utf-8")
        diff = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/repository.diff",
                headers=_headers("diff-1"),
                body={"resource_ref": repository_id},
            )
        )
        assert diff.status == 200
        assert isinstance(diff.body, dict)
        assert diff.body["changed_paths"] == ["README.md"]
        assert "two" in diff.body["patch"]

        committed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/repository.commit",
                headers=_headers("commit-1"),
                body={
                    "resource_ref": repository_id,
                    "message": "control-plane change",
                    "author_name": "Repository Test",
                    "author_email": "repository@example.invalid",
                },
            )
        )
        assert committed.status == 200
        assert isinstance(committed.body, dict)
        assert committed.body["repository_id"] == repository_id
        assert isinstance(committed.body["revision"], str)
        assert len(committed.body["revision"]) == 40

        rejected = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/repository.status",
                headers=_headers("status-invalid"),
                body={"resource_ref": repository_id, "provider_token": "must-not-pass"},
            )
        )
        assert rejected.status == 400
        assert isinstance(rejected.body, dict)
        assert rejected.body["code"] == "invalid_request"

    asyncio.run(scenario())
