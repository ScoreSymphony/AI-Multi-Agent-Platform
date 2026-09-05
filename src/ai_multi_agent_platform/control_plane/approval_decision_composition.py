"""Canonical northbound Approval decision contract for issue #214.

Approval lifecycle storage remains owned by #15.  This composition adds only the safe
northbound mutation seam needed by CLI/Web clients: a caller must bind the decision to
the exact stored requested-action digest and the decision is executed only through
``AuthorizationGate.decide_approval()``.
"""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, OperationControl
from ai_multi_agent_platform.security.approval_control_plane import ApprovalResourceService
from ai_multi_agent_platform.security.authorization import (
    ActorIdentity,
    ActorType,
    infer_actor_identity,
)
from ai_multi_agent_platform.security.enforcement import AuthorizationGate

from .conversation_current_composition import (
    AuthenticatedControlPlaneHTTP,
    ControlPlaneASGI,
    ControlPlaneHTTP,
    build_openapi as _build_current_openapi,
)
from .models import RequestContext
from .organization_audit_api import ControlPlane as _CurrentControlPlane

APPROVAL_APPROVE_COMMAND = "approval.approve"
APPROVAL_DENY_COMMAND = "approval.deny"
APPROVAL_DECISION_COMMANDS = (APPROVAL_APPROVE_COMMAND, APPROVAL_DENY_COMMAND)


class ControlPlane(_CurrentControlPlane):
    """Current Control Plane plus the #15-gated Approval decision seam."""

    def __init__(
        self,
        *args: Any,
        approval_gate: AuthorizationGate | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.approval_gate = approval_gate
        self._approval_decision_results: dict[
            tuple[str, str, str],
            tuple[tuple[bool, str, str | None], dict[str, JsonValue]],
        ] = {}
        if approval_gate is not None and "approvals" not in self.registered_collections:
            self.register_resource_service(
                "approvals",
                ApprovalResourceService(approval_gate.approvals),
            )

    @property
    def registered_commands(self) -> tuple[str, ...]:
        commands = super().registered_commands
        if self.approval_gate is None:
            return commands
        return tuple(sorted(set((*commands, *APPROVAL_DECISION_COMMANDS))))

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if command not in APPROVAL_DECISION_COMMANDS:
            return await super().execute_command(context, command, resource_ref, payload)
        return await self._execute_approval_decision(
            context,
            approval_id=resource_ref,
            approve=command == APPROVAL_APPROVE_COMMAND,
            payload=payload or {},
        )

    async def _execute_approval_decision(
        self,
        context: RequestContext,
        *,
        approval_id: str,
        approve: bool,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        gate = self.approval_gate
        if gate is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "canonical Approval decision contract is not configured",
            )
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

        record = gate.approvals.get(approval_id)
        if requested_digest != record.requested_action_digest:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Approval decision does not match the stored requested action",
                details={"approval_id": approval_id, "binding": "requested_action_digest"},
            )

        key = (approval_id, context.actor.principal_ref, context.idempotency_key)
        signature = (approve, requested_digest, comment)
        previous = self._approval_decision_results.get(key)
        if previous is not None:
            previous_signature, previous_result = previous
            if previous_signature != signature:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Idempotency-Key was already used for a different Approval decision",
                    details={"approval_id": approval_id},
                )
            return dict(previous_result)

        actor_type = _actor_type(context)
        operation = OperationContext(
            correlation_id=context.correlation_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            control=OperationControl(idempotency_key=context.idempotency_key),
        )
        await gate.decide_approval(
            approval_id,
            approver=ActorIdentity(context.actor.principal_ref, actor_type),
            approve=approve,
            operation=operation,
            comment=comment,
        )
        result = await ApprovalResourceService(gate.approvals).get_resource(context, approval_id)
        self._approval_decision_results[key] = (signature, dict(result))
        return result


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


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
    include_conversations: bool = False,
    include_approval_decisions: bool = False,
) -> dict[str, Any]:
    """Build the current schema and optionally advertise the #214 decision commands."""

    commands = extension_commands
    if include_approval_decisions:
        commands = tuple(sorted(set((*commands, *APPROVAL_DECISION_COMMANDS))))
    return _build_current_openapi(
        extension_collections=extension_collections,
        extension_commands=commands,
        include_conversations=include_conversations,
    )


__all__ = [
    "APPROVAL_APPROVE_COMMAND",
    "APPROVAL_DECISION_COMMANDS",
    "APPROVAL_DENY_COMMAND",
    "AuthenticatedControlPlaneHTTP",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "build_openapi",
]
