from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import (
    TASK_PROJECT_BULK_MOVE_COMMAND,
    TASK_PROJECT_MOVE_ACTION,
    TASK_PROJECT_MOVE_COMMAND,
    ControlPlane,
    RequestContext,
)
from ai_multi_agent_platform.control_plane.models import ActorContext
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    OrganizationService,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _context(key: str, *, owner_type: str = "user", owner_id: str = "user:alice") -> RequestContext:
    return RequestContext(
        request_id=f"request:{key}",
        correlation_id=f"correlation:{key}",
        actor=ActorContext(
            principal_ref=owner_id,
            owner_type=owner_type,  # type: ignore[arg-type]
            owner_id=owner_id,
        ),
        idempotency_key=key,
    )


def _stack(*, authorization=None, organizations: OrganizationService | None = None):
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=events,
        authorization=authorization,
        organization_service=organizations,
    )
    return control_plane, kernel, events


async def _project(control_plane: ControlPlane, key: str, owner_type: str, owner_id: str):
    return control_plane.scopes.create_project(
        key=key,
        name=key,
        owner_type=owner_type,  # type: ignore[arg-type]
        owner_id=owner_id,
    )


async def _task(
    kernel: PlatformKernel,
    key: str,
    project_id: str,
    *,
    owner_type: str = "user",
    owner_id: str = "user:alice",
):
    return await kernel.create_task(
        idempotency_key=key,
        title=key,
        objective=f"objective:{key}",
        owner_type=owner_type,  # type: ignore[arg-type]
        owner_id=owner_id,
        project_id=project_id,
    )


def test_simple_move_is_canonical_auditable_and_idempotent() -> None:
    async def scenario() -> None:
        control_plane, kernel, _ = _stack()
        source = await _project(control_plane, "source", "user", "user:alice")
        destination = await _project(control_plane, "destination", "user", "user:alice")
        task = await _task(kernel, "task", source.id)
        context = _context("move-once")

        moved = await control_plane.execute_command(
            context,
            TASK_PROJECT_MOVE_COMMAND,
            task.task_id,
            {"destination_project_id": destination.id},
        )
        revision = moved["revision"]
        assert moved["project_id"] == destination.id

        history = await kernel.history(task.task_id)
        move_event = history[-1]
        assert move_event.event_type == "task.project_reassigned"
        assert move_event.project_id == source.id
        assert move_event.payload["source_project_id"] == source.id
        assert move_event.payload["destination_project_id"] == destination.id
        assert move_event.payload["historical_scope_policy"] == (
            "retain_original_event_and_run_scope"
        )

        retried = await control_plane.execute_command(
            context,
            TASK_PROJECT_MOVE_COMMAND,
            task.task_id,
            {"destination_project_id": destination.id},
        )
        assert retried["revision"] == revision
        assert len(await kernel.history(task.task_id)) == len(history)

        third = await _project(control_plane, "third", "user", "user:alice")
        with pytest.raises(ContractError):
            await control_plane.execute_command(
                context,
                TASK_PROJECT_MOVE_COMMAND,
                task.task_id,
                {"destination_project_id": third.id},
            )

    asyncio.run(scenario())


class _DenyDestination(FakeAuthorizationProvider):
    def __init__(self, destination_project_id: str) -> None:
        super().__init__()
        self.destination_project_id = destination_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        denied = (
            request.action == TASK_PROJECT_MOVE_ACTION
            and request.context.project_id == self.destination_project_id
        )
        return AuthorizationDecision(
            allowed=not denied, reason="destination denied" if denied else None
        )


def test_destination_authorization_is_checked_before_move() -> None:
    async def scenario() -> None:
        control_plane, kernel, _ = _stack()
        source = await _project(control_plane, "source", "user", "user:alice")
        destination = await _project(control_plane, "destination", "user", "user:alice")
        authorization = _DenyDestination(destination.id)
        control_plane, kernel, _ = _stack(authorization=authorization)
        source = await _project(control_plane, "source", "user", "user:alice")
        destination = await _project(control_plane, "destination", "user", "user:alice")
        authorization.destination_project_id = destination.id
        task = await _task(kernel, "task", source.id)

        with pytest.raises(ContractError):
            await control_plane.execute_command(
                _context("unauthorized"),
                TASK_PROJECT_MOVE_COMMAND,
                task.task_id,
                {"destination_project_id": destination.id},
            )

        assert (await kernel.get_task(task.task_id)).task.project_id == source.id
        assert not any(
            event.event_type == "task.project_reassigned"
            for event in await kernel.history(task.task_id)
        )
        assert any(
            call.action == TASK_PROJECT_MOVE_ACTION and call.context.project_id == destination.id
            for call in authorization.calls
        )

    asyncio.run(scenario())


def test_incompatible_project_owners_fail_closed() -> None:
    async def scenario() -> None:
        control_plane, kernel, _ = _stack()
        source = await _project(control_plane, "source", "user", "user:alice")
        destination = await _project(control_plane, "destination", "user", "user:bob")
        task = await _task(kernel, "task", source.id)

        with pytest.raises(ContractError, match="ownership scopes are incompatible"):
            await control_plane.execute_command(
                _context("owner-mismatch"),
                TASK_PROJECT_MOVE_COMMAND,
                task.task_id,
                {"destination_project_id": destination.id},
            )
        assert (await kernel.get_task(task.task_id)).task.project_id == source.id

    asyncio.run(scenario())


def test_bidirectional_cross_organization_sharing_makes_projects_compatible() -> None:
    async def scenario() -> None:
        organization_repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(organization_repository)
        source_org = await organizations.create_organization(
            name="Source", owner_actor_id="user:source-owner"
        )
        destination_org = await organizations.create_organization(
            name="Destination", owner_actor_id="user:destination-owner"
        )
        control_plane, kernel, _ = _stack(organizations=organizations)
        source = await _project(control_plane, "source", "organization", source_org.id)
        destination = await _project(
            control_plane, "destination", "organization", destination_org.id
        )
        await organizations.set_resource_owner(
            resource_type="project",
            resource_id=source.id,
            owner_ref=OwnerRef(type="organization", id=source_org.id),
            organization_id=source_org.id,
        )
        await organizations.set_resource_owner(
            resource_type="project",
            resource_id=destination.id,
            owner_ref=OwnerRef(type="organization", id=destination_org.id),
            organization_id=destination_org.id,
        )
        await organizations.share_resource(
            resource_type="project",
            resource_id=source.id,
            target_ref=OwnerRef(type="organization", id=destination_org.id),
            granted_by_actor_id="user:source-owner",
            allow_cross_organization=True,
        )
        await organizations.share_resource(
            resource_type="project",
            resource_id=destination.id,
            target_ref=OwnerRef(type="organization", id=source_org.id),
            granted_by_actor_id="user:destination-owner",
            allow_cross_organization=True,
        )
        task = await _task(
            kernel,
            "task",
            source.id,
            owner_type="organization",
            owner_id=source_org.id,
        )

        moved = await control_plane.execute_command(
            _context(
                "shared-move",
                owner_type="organization",
                owner_id=source_org.id,
            ),
            TASK_PROJECT_MOVE_COMMAND,
            task.task_id,
            {"destination_project_id": destination.id},
        )
        assert moved["project_id"] == destination.id

    asyncio.run(scenario())


def test_parent_dependency_and_workspace_invariants_are_preflighted() -> None:
    async def scenario() -> None:
        control_plane, kernel, _ = _stack()
        source = await _project(control_plane, "source", "user", "user:alice")
        destination = await _project(control_plane, "destination", "user", "user:alice")
        parent = await _task(kernel, "parent", source.id)
        child = await _task(kernel, "child", source.id)
        prepared = await control_plane.task_management.prepare(
            child.task_id,
            {
                "parent_task_id": parent.task_id,
                "dependencies": [{"task_id": parent.task_id, "kind": "depends_on"}],
            },
        )
        await control_plane.task_management.commit(
            prepared,
            idempotency_key="child-relations",
            actor_ref="user:alice",
        )

        with pytest.raises(ContractError, match="cross-Project Task relationship"):
            await control_plane.execute_command(
                _context("move-child"),
                TASK_PROJECT_MOVE_COMMAND,
                child.task_id,
                {"destination_project_id": destination.id},
            )

        standalone = await _task(kernel, "workspace-task", source.id)
        workspace = control_plane.scopes.create_workspace(
            key="workspace",
            project_id=source.id,
        )
        prepared_workspace = await control_plane.task_management.prepare(
            standalone.task_id,
            {"workspace_id": workspace.id},
        )
        await control_plane.task_management.commit(
            prepared_workspace,
            idempotency_key="workspace-binding",
            actor_ref="user:alice",
        )
        with pytest.raises(ContractError, match="canonical Workspace"):
            await control_plane.execute_command(
                _context("move-workspace-task"),
                TASK_PROJECT_MOVE_COMMAND,
                standalone.task_id,
                {"destination_project_id": destination.id},
            )

    asyncio.run(scenario())


def test_bulk_preflight_rejects_connected_graph_without_partial_move() -> None:
    async def scenario() -> None:
        control_plane, kernel, _ = _stack()
        source = await _project(control_plane, "source", "user", "user:alice")
        destination = await _project(control_plane, "destination", "user", "user:alice")
        parent = await _task(kernel, "parent", source.id)
        child = await _task(kernel, "child", source.id)
        prepared = await control_plane.task_management.prepare(
            child.task_id,
            {"parent_task_id": parent.task_id},
        )
        await control_plane.task_management.commit(
            prepared,
            idempotency_key="parent-binding",
            actor_ref="user:alice",
        )

        with pytest.raises(ContractError, match="multi-stream atomic commits"):
            await control_plane.execute_command(
                _context("bulk-connected"),
                TASK_PROJECT_BULK_MOVE_COMMAND,
                "tasks",
                {
                    "moves": [
                        {"task_id": parent.task_id, "destination_project_id": destination.id},
                        {"task_id": child.task_id, "destination_project_id": destination.id},
                    ]
                },
            )

        assert (await kernel.get_task(parent.task_id)).task.project_id == source.id
        assert (await kernel.get_task(child.task_id)).task.project_id == source.id

    asyncio.run(scenario())


def test_bulk_move_of_independent_tasks_preflights_then_moves_all() -> None:
    async def scenario() -> None:
        control_plane, kernel, _ = _stack()
        source = await _project(control_plane, "source", "user", "user:alice")
        destination = await _project(control_plane, "destination", "user", "user:alice")
        first = await _task(kernel, "first", source.id)
        second = await _task(kernel, "second", source.id)

        result = await control_plane.execute_command(
            _context("bulk-independent"),
            TASK_PROJECT_BULK_MOVE_COMMAND,
            "tasks",
            {
                "moves": [
                    {"task_id": first.task_id, "destination_project_id": destination.id},
                    {"task_id": second.task_id, "destination_project_id": destination.id},
                ]
            },
        )
        assert result["atomic"] is False
        assert result["authorization_preflighted"] is True
        assert result["relationship_preflighted"] is True
        assert result["count"] == 2
        assert (await kernel.get_task(first.task_id)).task.project_id == destination.id
        assert (await kernel.get_task(second.task_id)).task.project_id == destination.id

    asyncio.run(scenario())


def test_historical_plan_run_artifact_result_scope_is_retained_and_retry_uses_destination() -> None:
    async def scenario() -> None:
        control_plane, kernel, _ = _stack()
        source = await _project(control_plane, "source", "user", "user:alice")
        destination = await _project(control_plane, "destination", "user", "user:alice")
        task = await _task(kernel, "task", source.id)
        await kernel.ready_task(idempotency_key="ready", task_id=task.task_id)
        planned = await kernel.plan_task(idempotency_key="plan", task_id=task.task_id)
        artifact_id = new_id("artifact")
        result_id = new_id("result")
        await kernel.attach_artifact(
            idempotency_key="artifact",
            task_id=task.task_id,
            artifact_id=artifact_id,
        )
        await kernel.attach_result(
            idempotency_key="result",
            task_id=task.task_id,
            result_id=result_id,
        )
        old_run = await kernel.create_run(
            idempotency_key="old-run",
            task_id=task.task_id,
        )
        await kernel.start_run(
            idempotency_key="old-start",
            task_id=task.task_id,
            run_id=old_run.run_id,
        )
        await kernel.record_run_outcome(
            idempotency_key="old-failed",
            task_id=task.task_id,
            run_id=old_run.run_id,
            status=RunStatus.FAILED,
        )

        await control_plane.execute_command(
            _context("historical-move"),
            TASK_PROJECT_MOVE_COMMAND,
            task.task_id,
            {"destination_project_id": destination.id},
        )
        moved = await kernel.get_task(task.task_id)
        historical_run = await kernel.get_run(task.task_id, old_run.run_id)
        assert moved.task.project_id == destination.id
        assert moved.plan_ref == planned.plan_ref
        assert artifact_id in moved.artifact_ids
        assert result_id in moved.result_ids
        assert historical_run.run.project_id == source.id

        history = await kernel.history(task.task_id)
        for event in history:
            if event.event_type in {
                "plan.created",
                "run.created",
                "artifact.attached",
                "result.attached",
            }:
                assert event.project_id == source.id

        new_run = await kernel.retry_task(
            idempotency_key="destination-retry",
            task_id=task.task_id,
        )
        assert new_run.run.project_id == destination.id
        latest_history = await kernel.history(task.task_id)
        destination_run_event = next(
            event
            for event in latest_history
            if event.event_type == "run.created" and event.subject_id == new_run.run_id
        )
        assert destination_run_event.project_id == destination.id

    asyncio.run(scenario())
