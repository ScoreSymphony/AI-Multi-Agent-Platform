"""Canonical authorization/approval gate used at server-side enforcement points."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    AuthorizationProvider,
    ContractError,
    ErrorCode,
    JsonValue,
    OperationContext,
    normalize_authorization_decision,
)

from .approvals import ApprovalRecord, ApprovalService
from .authorization import (
    ActorIdentity,
    AuthorizationAction,
    AuthorizationAuditRecord,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    RiskClassification,
)

type AuthorizationAuditSink = Callable[[AuthorizationAuditRecord], None]


class AuthorizationGate:
    """Evaluate policy, bind approvals to exact actions and emit value-free audit records."""

    def __init__(
        self,
        provider: AuthorizationProvider,
        *,
        approvals: ApprovalService | None = None,
        audit_sink: AuthorizationAuditSink | None = None,
    ) -> None:
        self.provider = provider
        self.approvals = approvals or ApprovalService()
        self._audit_sink = audit_sink
        self._audit_records: list[AuthorizationAuditRecord] = []

    @property
    def audit_records(self) -> tuple[AuthorizationAuditRecord, ...]:
        return tuple(self._audit_records)

    async def decide(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> AuthorizationDecision:
        digest = action.digest
        request = action.context.to_request(
            requested_action_digest=digest,
            approval_id=approval_id,
        )
        decision = normalize_authorization_decision(await self.provider.authorize(request))
        resolved_approval: ApprovalRecord | None = None

        if decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL:
            if approval_id is not None and self.approvals.valid_for(approval_id, action):
                resolved_approval = self.approvals.get(approval_id)
            elif approval_id is None:
                resolved_approval = self.approvals.find_valid_for(action)

            if resolved_approval is not None:
                allowed = AuthorizationDecision(
                    AuthorizationOutcome.ALLOW,
                    reason="exact action covered by approved approval",
                    policy_id=decision.policy_id,
                    constraints=decision.constraints,
                    audit_metadata=decision.audit_metadata,
                    adapter_metadata=decision.adapter_metadata,
                )
                self._audit(action, allowed, resolved_approval.approval_id)
                return allowed

            pending = self.approvals.pending_for(action)
            if pending is None:
                pending = self.approvals.request(
                    action,
                    reason=decision.reason or "authorization policy requires approval",
                    policy_id=decision.policy_id or "authorization:unspecified",
                    risk=risk,
                )
            gated = AuthorizationDecision(
                AuthorizationOutcome.REQUIRE_APPROVAL,
                reason=decision.reason,
                policy_id=decision.policy_id,
                constraints={
                    **dict(decision.constraints),
                    "approval_id": pending.approval_id,
                    "requested_action_digest": digest,
                },
                audit_metadata=decision.audit_metadata,
                adapter_metadata=decision.adapter_metadata,
            )
            self._audit(action, gated, pending.approval_id)
            return gated

        self._audit(action, decision, approval_id)
        return decision

    async def enforce(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> AuthorizationDecision:
        decision = await self.decide(action, approval_id=approval_id, risk=risk)
        if decision.outcome is AuthorizationOutcome.ALLOW:
            return decision
        details: dict[str, JsonValue] = {
            "authorization_outcome": decision.outcome.value,
            "policy_id": decision.policy_id,
        }
        details.update(decision.constraints)
        raise ContractError(
            ErrorCode.FORBIDDEN,
            decision.reason or "authorization denied",
            provider_id=self.provider.descriptor.provider_id,
            details=details,
        )

    async def decide_approval(
        self,
        approval_id: str,
        *,
        approver: ActorIdentity,
        approve: bool,
        operation: OperationContext,
        comment: str | None = None,
    ) -> ApprovalRecord:
        """Approve or reject only after the approver itself is authorized.

        Approval decisions deliberately do not recurse into another approval flow. The
        configured policy must return ``allow`` for the canonical ``approve`` action.
        """

        record = self.approvals.get(approval_id)
        resource_type = _resource_type(record.resource_type)
        action = ProposedAction(
            AuthorizationContext(
                actor=approver,
                action=AuthorizationAction.APPROVE,
                resource_type=resource_type,
                resource_id=record.resource_id,
                operation=operation,
                task_id=record.task_id,
                run_id=record.run_id,
                capability_ref=record.capability_ref,
                side_effect="approval_decision",
            ),
            payload={
                "approval_id": approval_id,
                "decision": "approve" if approve else "reject",
                "requested_action_digest": record.requested_action_digest,
            },
        )
        decision = normalize_authorization_decision(
            await self.provider.authorize(
                action.context.to_request(requested_action_digest=action.digest)
            )
        )
        self._audit(action, decision, approval_id)
        if decision.outcome is not AuthorizationOutcome.ALLOW:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                decision.reason or "approver is not authorized",
                provider_id=self.provider.descriptor.provider_id,
                details={
                    "authorization_outcome": decision.outcome.value,
                    "policy_id": decision.policy_id,
                    "approval_id": approval_id,
                },
            )
        return self.approvals._decide_authorized(
            approval_id,
            approver_ref=approver.actor_id,
            approve=approve,
            comment=comment,
        )

    async def cancel_approval(
        self,
        approval_id: str,
        *,
        actor: ActorIdentity,
        operation: OperationContext,
    ) -> ApprovalRecord:
        """Cancel a pending request as requester or as an authorized approver."""

        record = self.approvals.get(approval_id)
        if actor.actor_id != record.requester_ref:
            resource_type = _resource_type(record.resource_type)
            action = ProposedAction(
                AuthorizationContext(
                    actor=actor,
                    action=AuthorizationAction.APPROVE,
                    resource_type=resource_type,
                    resource_id=record.resource_id,
                    operation=operation,
                    task_id=record.task_id,
                    run_id=record.run_id,
                    capability_ref=record.capability_ref,
                    side_effect="approval_cancel",
                ),
                payload={"approval_id": approval_id, "decision": "cancel"},
            )
            decision = normalize_authorization_decision(
                await self.provider.authorize(
                    action.context.to_request(requested_action_digest=action.digest)
                )
            )
            self._audit(action, decision, approval_id)
            if decision.outcome is not AuthorizationOutcome.ALLOW:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    decision.reason or "actor cannot cancel this approval",
                    provider_id=self.provider.descriptor.provider_id,
                    details={"approval_id": approval_id},
                )
        return self.approvals._cancel_authorized(approval_id, actor_ref=actor.actor_id)

    def ensure_pending_approval(
        self,
        action: ProposedAction,
        *,
        reason: str,
        policy_id: str,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> ApprovalRecord:
        return self.approvals.request(
            action,
            reason=reason,
            policy_id=policy_id,
            risk=risk,
        )

    def _audit(
        self,
        action: ProposedAction,
        decision: AuthorizationDecision,
        approval_id: str | None,
    ) -> None:
        context = action.context
        record = AuthorizationAuditRecord(
            actor_ref=context.actor.actor_id,
            actor_type=context.actor.actor_type,
            action=context.action,
            resource_type=context.resource_type,
            resource_id=context.resource_id,
            outcome=decision.outcome,
            reason=decision.reason,
            policy_id=decision.policy_id,
            occurred_at=datetime.now(UTC),
            correlation_id=context.operation.correlation_id,
            project_id=context.operation.project_id,
            task_id=context.task_id,
            run_id=context.run_id,
            approval_id=approval_id,
            requested_action_digest=action.digest,
        )
        self._audit_records.append(record)
        if self._audit_sink is not None:
            self._audit_sink(record)


def _resource_type(value: str) -> ResourceType:
    try:
        return ResourceType(value)
    except ValueError:
        return ResourceType.GENERIC
