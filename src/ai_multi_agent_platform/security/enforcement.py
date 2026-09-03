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
    normalize_authorization_decision,
)

from .approvals import ApprovalRecord, ApprovalService
from .authorization import (
    AuthorizationAuditRecord,
    ProposedAction,
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
        details = {
            "authorization_outcome": decision.outcome.value,
            "policy_id": decision.policy_id,
        }
        details.update(dict(decision.constraints))
        raise ContractError(
            ErrorCode.FORBIDDEN,
            decision.reason or "authorization denied",
            provider_id=self.provider.descriptor.provider_id,
            details=details,
        )

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
