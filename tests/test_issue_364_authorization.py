from __future__ import annotations

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.workflows import (
    AuthorizedWorkflowService,
    InMemoryWorkflowRepository,
    WorkflowCallContext,
    WorkflowContent,
    WorkflowService,
    WorkflowStage,
)


def _content() -> WorkflowContent:
    return WorkflowContent(
        name="Authorized workflow",
        description="",
        stages=(WorkflowStage(stage_id="one", title="One"),),
    )


def _policy(
    principal_ref: str,
    *,
    project_id: str,
    organization_id: str,
) -> LocalPrincipalPolicy:
    return LocalPrincipalPolicy(
        principal_ref=principal_ref,
        actor_types=frozenset({ActorType.HUMAN}),
        allowed_actions=frozenset(
            {
                AuthorizationAction.CREATE,
                AuthorizationAction.READ,
                AuthorizationAction.MODIFY,
                AuthorizationAction.EXECUTE,
            }
        ),
        resource_types=frozenset({ResourceType.GENERIC}),
        project_ids=frozenset({project_id}),
        organization_ids=frozenset({organization_id}),
    )


@pytest.mark.asyncio
async def test_authorization_uses_persisted_scope_not_caller_claim() -> None:
    project_a = new_id("project")
    project_b = new_id("project")
    organization_a = "org-a"
    organization_b = "org-b"
    base = WorkflowService(InMemoryWorkflowRepository())
    revision = base.create(
        owner_ref=OwnerRef(type="user", id="alice"),
        content=_content(),
        project_id=project_a,
        organization_id=organization_a,
    )
    provider = LocalAuthorizationProvider(
        (
            _policy("user:alice", project_id=project_a, organization_id=organization_a),
            _policy("user:bob", project_id=project_b, organization_id=organization_b),
        )
    )
    service = AuthorizedWorkflowService(base, AuthorizationGate(provider))

    alice = WorkflowCallContext(
        operation=OperationContext(
            correlation_id="workflow-auth-alice",
            project_id=project_b,
        ),
        actor_ref="user:alice",
    )
    assert await service.get(revision.workflow_id, context=alice) == base.get(revision.workflow_id)

    bob = WorkflowCallContext(
        operation=OperationContext(
            correlation_id="workflow-auth-bob",
            project_id=project_b,
        ),
        actor_ref="user:bob",
    )
    with pytest.raises(ContractError) as exc_info:
        await service.get(revision.workflow_id, context=bob)
    assert exc_info.value.code is ErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_create_and_admission_are_authorization_gated() -> None:
    project_id = new_id("project")
    organization_id = "org-a"
    provider = LocalAuthorizationProvider(
        (_policy("user:alice", project_id=project_id, organization_id=organization_id),)
    )
    base = WorkflowService(InMemoryWorkflowRepository())
    service = AuthorizedWorkflowService(base, AuthorizationGate(provider))
    context = WorkflowCallContext(
        operation=OperationContext(correlation_id="workflow-create"),
        actor_ref="user:alice",
    )

    revision = await service.create(
        context=context,
        owner_ref=OwnerRef(type="user", id="alice"),
        content=_content(),
        project_id=project_id,
        organization_id=organization_id,
    )
    admission = await service.admit(
        revision.ref,
        context=context,
        task_id=new_id("task"),
        owner_ref=OwnerRef(type="user", id="alice"),
    )
    assert admission.source == revision.ref
    assert admission.plan.project_id == project_id
