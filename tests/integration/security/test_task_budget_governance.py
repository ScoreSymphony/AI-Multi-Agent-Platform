from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.accounting.service import AccountingService
from ai_multi_agent_platform.accounting.store import InMemoryUsageStore
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.execution.budgets import (
    BudgetConsumptionSource,
    BudgetDimension,
    InMemoryTaskBudgetStore,
    TaskBudgetEnforcementService,
    TaskBudgetLimit,
    TaskBudgetPolicy,
    TaskBudgetPolicyMutationService,
)
from ai_multi_agent_platform.execution.budgets.models import utc_now
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)

_TASK_ID = "task_budget_governance"


def _policy() -> TaskBudgetPolicy:
    return TaskBudgetPolicy(
        task_id=_TASK_ID,
        started_at=utc_now(),
        limits=(
            TaskBudgetLimit(
                dimension=BudgetDimension.MODEL_CALLS,
                limit=1.0,
                source=BudgetConsumptionSource.RUNTIME_COUNTER,
            ),
        ),
    )


def _operation(owner_type: str, owner_id: str) -> OperationContext:
    return OperationContext(
        correlation_id=_TASK_ID,
        owner_type=owner_type,
        owner_id=owner_id,
    )


def _runtime(
    gate: AuthorizationGate,
) -> tuple[TaskBudgetEnforcementService, TaskBudgetPolicyMutationService]:
    budgets = TaskBudgetEnforcementService(
        InMemoryTaskBudgetStore(),
        AccountingService(InMemoryUsageStore()),
    )
    return budgets, TaskBudgetPolicyMutationService(budgets, gate)


@pytest.mark.asyncio
async def test_authorized_human_revision_is_versioned_and_audited() -> None:
    gate = AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:operator",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.MODIFY}),
                    resource_types=frozenset({ResourceType.TASK}),
                ),
            )
        )
    )
    budgets, mutations = _runtime(gate)
    first = _policy()
    await budgets.put_policy(first)
    candidate = replace(
        first,
        version=2,
        limits=(replace(first.limits[0], limit=3.0),),
        updated_at=utc_now(),
    )

    snapshot = await mutations.revise(
        candidate,
        actor=ActorIdentity("user:operator", ActorType.HUMAN),
        operation=_operation("user", "operator"),
    )

    assert snapshot.policy_version == 2
    stored = await budgets.policy(first.task_id)
    assert stored is not None
    assert stored.started_at == first.started_at
    assert stored.provenance["budget_mutation_actor"] == "user:operator"
    assert stored.provenance["budget_mutation_actor_type"] == ActorType.HUMAN.value
    assert len(gate.audit_records) == 1


@pytest.mark.asyncio
async def test_agent_cannot_self_grant_but_exact_approved_revision_can_apply() -> None:
    gate = AuthorizationGate(
        LocalAuthorizationProvider(
            (
                # Deliberately permissive MODIFY policy: the budget service must still force an
                # independent Approval for an Agent-originated increase.
                LocalPrincipalPolicy(
                    principal_ref="agent:budgeter",
                    actor_types=frozenset({ActorType.AGENT}),
                    allowed_actions=frozenset({AuthorizationAction.MODIFY}),
                    resource_types=frozenset({ResourceType.TASK}),
                ),
                LocalPrincipalPolicy(
                    principal_ref="user:reviewer",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                    resource_types=frozenset({ResourceType.TASK}),
                ),
            )
        )
    )
    budgets, mutations = _runtime(gate)
    first = _policy()
    await budgets.put_policy(first)
    approved_candidate = replace(
        first,
        version=2,
        limits=(replace(first.limits[0], limit=2.0),),
        updated_at=utc_now(),
    )
    agent = ActorIdentity("agent:budgeter", ActorType.AGENT)

    with pytest.raises(ContractError) as pending:
        await mutations.revise(
            approved_candidate,
            actor=agent,
            operation=_operation("agent", "budgeter"),
        )
    assert pending.value.code is ErrorCode.FORBIDDEN
    approval_id = str(pending.value.details["approval_id"])
    unchanged = await budgets.policy(first.task_id)
    assert unchanged is not None
    assert unchanged.version == 1

    await gate.decide_approval(
        approval_id,
        approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
        approve=True,
        operation=_operation("user", "reviewer"),
    )

    changed_candidate = replace(
        approved_candidate,
        limits=(replace(first.limits[0], limit=4.0),),
        updated_at=utc_now(),
    )
    with pytest.raises(ContractError) as changed:
        await mutations.revise(
            changed_candidate,
            actor=agent,
            operation=_operation("agent", "budgeter"),
            approval_id=approval_id,
        )
    assert changed.value.code is ErrorCode.FORBIDDEN
    assert changed.value.details["approval_id"] != approval_id

    snapshot = await mutations.revise(
        approved_candidate,
        actor=agent,
        operation=_operation("agent", "budgeter"),
        approval_id=approval_id,
    )
    assert snapshot.policy_version == 2
    stored = await budgets.policy(first.task_id)
    assert stored is not None
    assert stored.limits[0].limit == 2.0
    assert stored.provenance["budget_mutation_approval_id"] == approval_id


@pytest.mark.asyncio
async def test_revision_cannot_reset_runtime_origin() -> None:
    gate = AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:operator",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.MODIFY}),
                    resource_types=frozenset({ResourceType.TASK}),
                ),
            )
        )
    )
    budgets, mutations = _runtime(gate)
    first = _policy()
    await budgets.put_policy(first)
    candidate = replace(first, version=2, started_at=utc_now(), updated_at=utc_now())

    with pytest.raises(ContractError) as exc_info:
        await mutations.revise(
            candidate,
            actor=ActorIdentity("user:operator", ActorType.HUMAN),
            operation=_operation("user", "operator"),
        )

    assert exc_info.value.code is ErrorCode.INVALID_REQUEST
    stored = await budgets.policy(first.task_id)
    assert stored is not None
    assert stored.version == 1


@pytest.mark.asyncio
async def test_authorized_human_can_create_initial_policy() -> None:
    gate = AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:operator",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.CREATE}),
                    resource_types=frozenset({ResourceType.TASK}),
                ),
            )
        )
    )
    budgets, mutations = _runtime(gate)
    policy = _policy()

    snapshot = await mutations.configure(
        policy,
        actor=ActorIdentity("user:operator", ActorType.HUMAN),
        operation=_operation("user", "operator"),
    )

    assert snapshot.policy_version == 1
    stored = await budgets.policy(policy.task_id)
    assert stored is not None
    assert stored.provenance["budget_mutation_actor"] == "user:operator"
