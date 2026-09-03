from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.control_plane.task_management_contract import (
    TASK_MANAGEMENT_UPDATE_COMMAND,
)
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import ApprovalStatus, OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
    canonical_control_plane_vocabulary,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    SqliteWorkspaceProvider,
    WorkspaceType,
)


def _kernel() -> tuple[InMemoryKernelRepository, PlatformKernel]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return repository, kernel


def _request_context(key: str = "issue-15-final") -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        actor=ActorContext(
            principal_ref="user:test",
            owner_type="user",
            owner_id="test",
        ),
        idempotency_key=key,
    )


def _approval_stack(
    *,
    approval_actions: frozenset[AuthorizationAction],
    allowed_actions: frozenset[AuthorizationAction] = frozenset({AuthorizationAction.READ}),
    resource_types: frozenset[ResourceType] = frozenset(),
) -> tuple[AuthorizationGate, ControlPlaneAuthorizationBridge]:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:test",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=allowed_actions,
                approval_actions=approval_actions,
                resource_types=resource_types,
            ),
            LocalPrincipalPolicy(
                principal_ref="user:reviewer",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    return gate, ControlPlaneAuthorizationBridge(gate)


async def _approve_latest(gate: AuthorizationGate, *, project_id: str | None = None) -> str:
    record = gate.approvals.all()[-1]
    await gate.decide_approval(
        record.approval_id,
        approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
        approve=True,
        operation=OperationContext(
            correlation_id="correlation-review-final",
            owner_type="user",
            owner_id="reviewer",
            project_id=project_id,
        ),
    )
    return record.approval_id


def test_generic_extension_command_approval_binds_exact_payload() -> None:
    async def scenario() -> None:
        repository, kernel = _kernel()
        gate, bridge = _approval_stack(
            approval_actions=frozenset({AuthorizationAction.MODIFY}),
        )
        calls: list[dict[str, object]] = []

        async def mutate(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            del context
            calls.append(dict(payload))
            return {"id": resource_ref, "value": payload.get("value")}

        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=bridge,
            command_handlers={"custom.mutate": mutate},
        )
        context = _request_context("generic-payload")
        payload_a = {"value": "A"}
        payload_b = {"value": "B"}

        with pytest.raises(ContractError):
            await control_plane.execute_command(context, "custom.mutate", "resource:1", payload_a)
        first = gate.approvals.all()[0]
        assert calls == []
        await _approve_latest(gate)

        result = await control_plane.execute_command(
            context,
            "custom.mutate",
            "resource:1",
            payload_a,
        )
        assert result["value"] == "A"
        assert calls == [payload_a]

        with pytest.raises(ContractError):
            await control_plane.execute_command(context, "custom.mutate", "resource:1", payload_b)
        approvals = gate.approvals.all()
        assert len(approvals) == 2
        assert approvals[1].requested_action_digest != first.requested_action_digest
        assert calls == [payload_a]

    asyncio.run(scenario())


def test_task_management_update_approval_binds_exact_payload() -> None:
    async def scenario() -> None:
        repository, kernel = _kernel()
        task = await kernel.create_task(
            idempotency_key="create-managed-task",
            title="Managed",
            objective="Verify exact update binding",
            owner_type="user",
            owner_id="test",
            actor_ref="user:test",
        )
        gate, bridge = _approval_stack(
            approval_actions=frozenset({AuthorizationAction.MODIFY}),
            allowed_actions=frozenset({AuthorizationAction.READ}),
            resource_types=frozenset({ResourceType.TASK}),
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=bridge,
        )
        context = _request_context("task-management-payload")
        payload_a = {"priority": "high"}
        payload_b = {"priority": "urgent"}

        with pytest.raises(ContractError):
            await control_plane.execute_command(
                context,
                TASK_MANAGEMENT_UPDATE_COMMAND,
                task.task_id,
                payload_a,
            )
        first = gate.approvals.all()[0]
        await _approve_latest(gate)

        updated = await control_plane.execute_command(
            context,
            TASK_MANAGEMENT_UPDATE_COMMAND,
            task.task_id,
            payload_a,
        )
        assert updated["priority"] == "high"

        with pytest.raises(ContractError):
            await control_plane.execute_command(
                context,
                TASK_MANAGEMENT_UPDATE_COMMAND,
                task.task_id,
                payload_b,
            )
        approvals = gate.approvals.all()
        assert len(approvals) == 2
        assert approvals[1].requested_action_digest != first.requested_action_digest
        loaded = await control_plane.get_task(context, task.task_id)
        assert loaded["priority"] == "high"

    asyncio.run(scenario())


def test_workspace_create_approval_binds_composed_workspace_payload(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository, kernel = _kernel()
        gate, bridge = _approval_stack(
            approval_actions=frozenset({AuthorizationAction.CREATE}),
            allowed_actions=frozenset({AuthorizationAction.READ}),
            resource_types=frozenset({ResourceType.WORKSPACE}),
        )
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        workspaces = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=bridge,
            workspace_provider=workspaces,
        )
        project = control_plane.scopes.create_project(
            key="direct-project",
            name="Authorization project",
            owner_type="user",
            owner_id="test",
        )
        context = _request_context("workspace-payload")
        payload_a = {
            "project_id": project.id,
            "workspace_type": "persistent_project",
        }
        payload_b = {
            "project_id": project.id,
            "workspace_type": "isolated_run",
        }

        with pytest.raises(ContractError):
            await control_plane.create_workspace(context, payload_a)
        first = gate.approvals.all()[0]
        await _approve_latest(gate, project_id=project.id)
        created = await control_plane.create_workspace(context, payload_a)
        assert created["workspace_type"] == "persistent_project"

        with pytest.raises(ContractError):
            await control_plane.create_workspace(context, payload_b)
        approvals = gate.approvals.all()
        assert len(approvals) == 2
        assert approvals[1].requested_action_digest != first.requested_action_digest

    asyncio.run(scenario())


def test_workspace_bound_start_approval_binds_resolved_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository, kernel = _kernel()
        gate, bridge = _approval_stack(
            approval_actions=frozenset({AuthorizationAction.EXECUTE}),
            allowed_actions=frozenset(
                {
                    AuthorizationAction.MODIFY,
                    AuthorizationAction.READ,
                }
            ),
        )
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        workspaces = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )
        bindings = InMemoryRunWorkspaceBindingRepository()
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=bridge,
            workspace_provider=workspaces,
            run_workspace_bindings=bindings,
        )
        project = control_plane.scopes.create_project(
            key="snapshot-project",
            name="Snapshot project",
            owner_type="user",
            owner_id="test",
        )
        data_context = DataAccessContext(
            operation=OperationContext(
                correlation_id="workspace-create-direct",
                owner_type="user",
                owner_id="test",
                project_id=project.id,
            ),
            actor_ref="user:test",
        )
        workspace = await workspaces.create_workspace(
            project_id=project.id,
            owner_ref=OwnerRef(type="user", id="test"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=data_context,
        )
        first_snapshot_id = workspace.base_snapshot_id
        assert first_snapshot_id is not None
        second_snapshot = await workspaces.create_snapshot(workspace.id)
        assert second_snapshot.id != first_snapshot_id

        task = await kernel.create_task(
            idempotency_key="snapshot-task-create",
            title="Snapshot task",
            objective="Verify exact run input binding",
            owner_type="user",
            owner_id="test",
            project_id=project.id,
            actor_ref="user:test",
        )
        await kernel.ready_task(
            idempotency_key="snapshot-task-ready",
            task_id=task.task_id,
            actor_ref="user:test",
        )
        context = _request_context("snapshot-start")
        payload_a = {
            "workspace_id": workspace.id,
            "workspace_snapshot_id": first_snapshot_id,
        }
        payload_b = {
            "workspace_id": workspace.id,
            "workspace_snapshot_id": second_snapshot.id,
        }

        with pytest.raises(ContractError):
            await control_plane.start_task(context, task.task_id, payload_a)
        first = gate.approvals.all()[0]
        await _approve_latest(gate, project_id=project.id)
        started = await control_plane.start_task(context, task.task_id, payload_a)
        assert started["workspace_snapshot_id"] == first_snapshot_id

        with pytest.raises(ContractError):
            await control_plane.start_task(context, task.task_id, payload_b)
        approvals = gate.approvals.all()
        assert len(approvals) == 2
        assert approvals[1].requested_action_digest != first.requested_action_digest

    asyncio.run(scenario())


def test_approval_decision_cannot_substitute_another_project_scope() -> None:
    project_a = new_id("project")
    project_b = new_id("project")
    task_id = new_id("task")
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:test",
                actor_types=frozenset({ActorType.HUMAN}),
                approval_actions=frozenset({AuthorizationAction.EXECUTE}),
                resource_types=frozenset({ResourceType.TASK}),
                project_ids=frozenset({project_b}),
            ),
            LocalPrincipalPolicy(
                principal_ref="user:reviewer",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                resource_types=frozenset({ResourceType.TASK}),
                project_ids=frozenset({project_a}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    proposed = ProposedAction(
        AuthorizationContext(
            actor=ActorIdentity("user:test", ActorType.HUMAN),
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.TASK,
            resource_id=task_id,
            operation=OperationContext(
                correlation_id="cross-project-request",
                owner_type="user",
                owner_id="test",
                project_id=project_b,
            ),
            task_id=task_id,
        )
    )
    decision = asyncio.run(gate.decide(proposed))
    approval_id = decision.constraints["approval_id"]
    assert isinstance(approval_id, str)

    with pytest.raises(ContractError) as mismatched:
        asyncio.run(
            gate.decide_approval(
                approval_id,
                approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
                approve=True,
                operation=OperationContext(
                    correlation_id="cross-project-review-a",
                    owner_type="user",
                    owner_id="reviewer",
                    project_id=project_a,
                ),
            )
        )
    assert mismatched.value.code is ErrorCode.FORBIDDEN

    with pytest.raises(ContractError) as forced_scope:
        asyncio.run(
            gate.decide_approval(
                approval_id,
                approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
                approve=True,
                operation=OperationContext(
                    correlation_id="cross-project-review-none",
                    owner_type="user",
                    owner_id="reviewer",
                ),
            )
        )
    assert forced_scope.value.code is ErrorCode.FORBIDDEN
    assert gate.approvals.get(approval_id).status is ApprovalStatus.PENDING


def test_composed_control_plane_is_fail_closed_without_explicit_dev_opt_out(monkeypatch) -> None:
    repository, kernel = _kernel()
    monkeypatch.delenv("AI_MULTI_AGENT_PLATFORM_ALLOW_INSECURE_CONTROL_PLANE", raising=False)

    with pytest.raises(ValueError, match="authorization is required"):
        ControlPlane(kernel=kernel, events=repository)

    monkeypatch.setenv("AI_MULTI_AGENT_PLATFORM_ALLOW_INSECURE_CONTROL_PLANE", "1")
    insecure = ControlPlane(kernel=kernel, events=repository)
    assert insecure.authorization_configured is False


def test_task_management_vocabulary_is_canonical_task_modify() -> None:
    assert canonical_control_plane_vocabulary("task-management.update") == (
        AuthorizationAction.MODIFY,
        ResourceType.TASK,
    )
    assert canonical_control_plane_vocabulary("task-management.bulk-update") == (
        AuthorizationAction.MODIFY,
        ResourceType.TASK,
    )
