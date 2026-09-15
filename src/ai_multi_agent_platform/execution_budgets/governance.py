"""Authorization and Approval protected mutation of per-Task execution budgets."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts import (
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    JsonValue,
    OperationContext,
)
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    RiskClassification,
)

from .models import TaskBudgetPolicy, TaskBudgetSnapshot, utc_now
from .service import TaskBudgetEnforcementService


class TaskBudgetPolicyMutationService:
    """Apply exact, versioned Task-budget revisions behind the canonical security gate.

    Agents may initiate a budget increase/override request, but an Agent can never turn an
    authorization-policy ``allow`` into a silent self-grant.  Agent-originated mutations require
    an independently approved Approval record bound to the exact proposed policy revision.
    """

    def __init__(
        self,
        budgets: TaskBudgetEnforcementService,
        authorization: AuthorizationGate,
    ) -> None:
        self._budgets = budgets
        self._authorization = authorization

    async def revise(
        self,
        candidate: TaskBudgetPolicy,
        *,
        actor: ActorIdentity,
        operation: OperationContext,
        approval_id: str | None = None,
    ) -> TaskBudgetSnapshot:
        current = await self._budgets.policy(candidate.task_id)
        if current is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "Task execution budget policy does not exist",
                details={"task_id": candidate.task_id},
            )
        if candidate.version != current.version + 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Task execution budget revision must advance exactly by one version",
                details={
                    "task_id": candidate.task_id,
                    "current_version": current.version,
                    "requested_version": candidate.version,
                },
            )
        if candidate.started_at != current.started_at:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Task execution budget revision cannot reset the Task runtime origin",
                details={"task_id": candidate.task_id},
            )

        action = _revision_action(
            current=current,
            candidate=candidate,
            actor=actor,
            operation=operation,
        )

        # An Agent can ask for more budget, but cannot silently grant itself more budget even if a
        # permissive provider policy would otherwise return ALLOW.  The exact proposed action must
        # have an independently approved Approval record.
        if actor.actor_type is ActorType.AGENT:
            approved = await self._authorization.runtime_approvals.resolve_valid_for(
                action,
                approval_id=approval_id,
            )
            if approved is None:
                pending = await self._authorization.ensure_pending_approval_with_event(
                    action,
                    reason="Agent-originated Task budget changes require independent Approval",
                    policy_id="task-budget:agent-independent-approval",
                    risk=RiskClassification.HIGH,
                )
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "Agent-originated Task budget changes require independent Approval",
                    details={
                        "authorization_outcome": AuthorizationOutcome.REQUIRE_APPROVAL.value,
                        "approval_id": pending.approval_id,
                        "requested_action_digest": action.digest,
                        "task_id": candidate.task_id,
                    },
                )

        await self._authorization.enforce(
            action,
            approval_id=approval_id,
            risk=RiskClassification.HIGH,
        )

        provenance = dict(candidate.provenance)
        provenance.update(
            {
                "budget_mutation_actor": actor.actor_id,
                "budget_mutation_actor_type": actor.actor_type.value,
                "budget_mutation_action_digest": action.digest,
            }
        )
        if approval_id is not None:
            provenance["budget_mutation_approval_id"] = approval_id

        authorized = replace(
            candidate,
            provenance=provenance,
            updated_at=utc_now(),
        )
        return await self._budgets.put_policy(authorized)


def _revision_action(
    *,
    current: TaskBudgetPolicy,
    candidate: TaskBudgetPolicy,
    actor: ActorIdentity,
    operation: OperationContext,
) -> ProposedAction:
    return ProposedAction(
        AuthorizationContext(
            actor=actor,
            action=AuthorizationAction.MODIFY,
            resource_type=ResourceType.TASK,
            resource_id=candidate.task_id,
            operation=operation,
            task_id=candidate.task_id,
            side_effect="task_budget_policy_revision",
        ),
        payload={
            "task_id": candidate.task_id,
            "current_version": current.version,
            "requested_version": candidate.version,
            "limits": [_limit_payload(limit) for limit in candidate.limits],
        },
    )


def _limit_payload(limit: object) -> dict[str, JsonValue]:
    # Kept structural rather than accepting arbitrary caller metadata so the Approval digest binds
    # only to canonical budget semantics.
    from .models import TaskBudgetLimit

    if not isinstance(limit, TaskBudgetLimit):
        raise TypeError("Task budget policy contains an invalid limit")
    return {
        "dimension": limit.dimension.value,
        "limit": limit.limit,
        "source": limit.source.value,
        "metric_type": limit.metric_type,
        "unit": limit.unit,
        "warning_fraction": limit.warning_fraction,
        "include_estimated": limit.include_estimated,
        "exhaustion_action": limit.exhaustion_action.value,
        "unavailable_policy": limit.unavailable_policy.value,
    }


__all__ = ["TaskBudgetPolicyMutationService"]
