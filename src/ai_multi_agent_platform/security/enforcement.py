"""Canonical authorization/approval gate used at server-side enforcement points."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
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
type ApprovalEventSink = Callable[[str, ApprovalRecord], Awaitable[None]]


class AuthorizationGate:
    """Evaluate policy, bind approvals to exact actions and emit value-free audit records."""

    def __init__(
        self,
        provider: AuthorizationProvider,
        *,
        approvals: ApprovalService | None = None,
        audit_sink: AuthorizationAuditSink | None = None,
        approval_event_sink: ApprovalEventSink | None = None,
    ) -> None:
        self.provider = provider
        self.approvals = approvals or ApprovalService()
        self._audit_sink = audit_sink
        self._approval_event_sinks: list[ApprovalEventSink] = []
        if approval_event_sink is not None:
            self._approval_event_sinks.append(approval_event_sink)
        self._audit_records: list[AuthorizationAuditRecord] = []

    @property
    def audit_records(self) -> tuple[AuthorizationAuditRecord, ...]:
        return tuple(self._audit_records)

    def add_approval_event_sink(self, sink: ApprovalEventSink) -> None:
        """Attach a best-effort lifecycle observer without changing Approval authority."""

        self._approval_event_sinks.append(sink)

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
            created = pending is None
            if pending is None:
                pending = self.approvals.request(
                    action,
                    reason=decision.reason or "authorization policy requires approval",
                    policy_id=decision.policy_id or "authorization:unspecified",
                    risk=risk,
                )
            if created:
                await self._emit_approval("required", pending)
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
        Security-relevant scope is derived from the stored Approval record; callers may
        supply correlation/owner/control metadata but cannot substitute another project.
        """

        record = self.approvals.get(approval_id)
        scoped_operation = _approval_operation(record, operation)
        resource_type = _resource_type(record.resource_type)
        action = ProposedAction(
            AuthorizationContext(
                actor=approver,
                action=AuthorizationAction.APPROVE,
                resource_type=resource_type,
                resource_id=record.resource_id,
                operation=scoped_operation,
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
        updated = self.approvals._decide_authorized(
            approval_id,
            approver_ref=approver.actor_id,
            approve=approve,
            comment=comment,
        )
        await self._emit_approval("resolved", updated)
        return updated

    async def cancel_approval(
        self,
        approval_id: str,
        *,
        actor: ActorIdentity,
        operation: OperationContext,
    ) -> ApprovalRecord:
        """Cancel a pending request as requester or as an authorized approver."""

        record = self.approvals.get(approval_id)
        scoped_operation = _approval_operation(record, operation)
        resource_type = _resource_type(record.resource_type)
        action = ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=AuthorizationAction.APPROVE,
                resource_type=resource_type,
                resource_id=record.resource_id,
                operation=scoped_operation,
                task_id=record.task_id,
                run_id=record.run_id,
                capability_ref=record.capability_ref,
                side_effect="approval_cancel",
            ),
            payload={
                "approval_id": approval_id,
                "decision": "cancel",
                "requested_action_digest": record.requested_action_digest,
            },
        )
        if actor.actor_id == record.requester_ref:
            decision = AuthorizationDecision(
                AuthorizationOutcome.ALLOW,
                reason="approval requester may cancel its own pending request",
                policy_id="approval:requester-cancel",
            )
        else:
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
        updated = self.approvals._cancel_authorized(approval_id, actor_ref=actor.actor_id)
        await self._emit_approval("resolved", updated)
        return updated

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

    async def ensure_pending_approval_with_event(
        self,
        action: ProposedAction,
        *,
        reason: str,
        policy_id: str,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> ApprovalRecord:
        """Create a pending Approval and publish its best-effort required-attention event."""

        existing = self.approvals.pending_for(action)
        if existing is not None:
            return existing
        record = self.ensure_pending_approval(
            action,
            reason=reason,
            policy_id=policy_id,
            risk=risk,
        )
        await self._emit_approval("required", record)
        return record

    async def _emit_approval(self, event: str, record: ApprovalRecord) -> None:
        for sink in tuple(self._approval_event_sinks):
            try:
                await sink(event, record)
            except Exception:
                # Approval state is authoritative and may already be committed. A downstream
                # attention observer must never turn that successful state transition into a
                # false authorization/approval failure.
                continue

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


def _approval_operation(record: ApprovalRecord, supplied: OperationContext) -> OperationContext:
    """Preserve request metadata while forcing the Approval's stored project scope."""

    if (
        supplied.project_id is not None
        and record.project_id is not None
        and supplied.project_id != record.project_id
    ):
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "approval decision scope does not match the stored approval project",
            details={
                "approval_id": record.approval_id,
                "approval_project_id": record.project_id,
                "supplied_project_id": supplied.project_id,
            },
        )
    return OperationContext(
        correlation_id=supplied.correlation_id,
        causation_id=supplied.causation_id,
        owner_type=supplied.owner_type,
        owner_id=supplied.owner_id,
        project_id=record.project_id,
        control=supplied.control,
    )


def _resource_type(value: str) -> ResourceType:
    try:
        return ResourceType(value)
    except ValueError:
        return ResourceType.GENERIC
