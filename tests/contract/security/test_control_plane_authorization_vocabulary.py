"""Migrated under #722; original coverage tracked issue #15."""


# ruff: noqa: F401

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


def test_task_management_vocabulary_is_canonical_task_modify() -> None:
    assert canonical_control_plane_vocabulary("task-management.update") == (
        AuthorizationAction.MODIFY,
        ResourceType.TASK,
    )
    assert canonical_control_plane_vocabulary("task-management.bulk-update") == (
        AuthorizationAction.MODIFY,
        ResourceType.TASK,
    )
