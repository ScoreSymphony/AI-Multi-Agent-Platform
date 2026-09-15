"""Explicit Control Plane ownership for northbound Approval decisions."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, OperationControl
from ai_multi_agent_platform.security.approval_control_plane import ApprovalResourceService
from ai_multi_agent_platform.security.authorization import (
    ActorIdentity,
    ActorType,
    infer_actor_identity,
)
from ai_multi_agent_platform.security.enforcement import AuthorizationGate

from .extensions import ControlPlaneModule
from .models import RequestContext

APPROVAL_APPROVE_COMMAND = "approval.approve"
APPROVAL_DENY_COMMAND = "approval.deny"
APPROVAL_DECISION_COMMANDS = (APPROVAL_APPROVE_COMMAND, APPROVAL_DENY_COMMAND)
APPROVAL_DECISION_MODULE = "approval-decisions"


class ApprovalDecisionBinding:
    """Northbound adapter around the canonical #15 Approval authority."""

    def __init__(self, gate: AuthorizationGate) -> None:
        self.gate = gate
        self._decision_results: dict[
            tuple[str, str, str],
            tuple[tuple[bool, str, str | None], dict[str, JsonValue]],
        ] = {}

    async def authorize_through_gate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> None:
        """Declare that Approval authorization is owned by ``AuthorizationGate``.

        The pre-#982 contract intentionally did not call the generic Control Plane
        ``_authorize(command, resource_ref)`` path for Approval decisions. The gate's
        ``decide_approval`` operation validates the approver and exact stored action.
        Keeping this explicit authorizer prevents module migration from adding a second,
        behavior-changing authorization decision ahead of that canonical authority.
        """

        del context, resource_ref, payload

    async def approve(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._execute(
            context,
            approval_id=resource_ref,
            approve=True,
            payload=payload,
        )

    async def deny(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._execute(
            context,
            approval_id=resource_ref,
            approve=False,
            payload=payload,
        )

    async def _execute(
        self,
        context: RequestContext,
        *,
        approval_id: str,
        approve: bool,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for Approval decisions",
                details={"header": "Idempotency-Key"},
            )

        requested_digest = payload.get("requested_action_digest")
        if not isinstance(requested_digest, str) or not requested_digest.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "requested_action_digest must be a non-blank string",
                details={"field": "requested_action_digest"},
            )
        comment_value = payload.get("comment")
        if comment_value is not None and (
            not isinstance(comment_value, str) or not comment_value.strip()
        ):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "comment must be a non-blank string when provided",
                details={"field": "comment"},
            )
        comment = comment_value if isinstance(comment_value, str) else None

        record = await self.gate.runtime_approvals.get(approval_id)
        if requested_digest != record.requested_action_digest:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Approval decision does not match the stored requested action",
                details={"approval_id": approval_id, "binding": "requested_action_digest"},
            )

        key = (approval_id, context.actor.principal_ref, context.idempotency_key)
        signature = (approve, requested_digest, comment)
        previous = self._decision_results.get(key)
        if previous is not None:
            previous_signature, previous_result = previous
            if previous_signature != signature:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Idempotency-Key was already used for a different Approval decision",
                    details={"approval_id": approval_id},
                )
            return dict(previous_result)

        operation = OperationContext(
            correlation_id=context.correlation_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            control=OperationControl(idempotency_key=context.idempotency_key),
        )
        await self.gate.decide_approval(
            approval_id,
            approver=ActorIdentity(context.actor.principal_ref, _actor_type(context)),
            approve=approve,
            operation=operation,
            comment=comment,
        )
        result = await ApprovalResourceService(
            self.gate.approvals,
            runtime_approvals=self.gate.runtime_approvals,
        ).get_resource(context, approval_id)
        self._decision_results[key] = (signature, dict(result))
        return result


def approval_decision_control_plane_module(
    gate: AuthorizationGate,
) -> tuple[ControlPlaneModule, ApprovalDecisionBinding]:
    """Build the explicitly owned Approval read/decision contribution."""

    binding = ApprovalDecisionBinding(gate)
    module = ControlPlaneModule(
        name=APPROVAL_DECISION_MODULE,
        resource_services={
            "approvals": ApprovalResourceService(
                gate.approvals,
                runtime_approvals=gate.runtime_approvals,
            )
        },
        command_handlers={
            APPROVAL_APPROVE_COMMAND: binding.approve,
            APPROVAL_DENY_COMMAND: binding.deny,
        },
        command_authorizers={
            APPROVAL_APPROVE_COMMAND: binding.authorize_through_gate,
            APPROVAL_DENY_COMMAND: binding.authorize_through_gate,
        },
    )
    return module, binding


def _actor_type(context: RequestContext) -> ActorType:
    if context.actor.actor_type is not None:
        try:
            return ActorType(context.actor.actor_type)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "authenticated actor type is not canonical",
            ) from exc
    return infer_actor_identity(context.actor.principal_ref).actor_type


__all__ = [
    "APPROVAL_APPROVE_COMMAND",
    "APPROVAL_DECISION_COMMANDS",
    "APPROVAL_DECISION_MODULE",
    "APPROVAL_DENY_COMMAND",
    "ApprovalDecisionBinding",
    "approval_decision_control_plane_module",
]
