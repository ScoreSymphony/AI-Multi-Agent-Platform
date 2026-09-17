"""Canonical authorization/approval gate used at server-side enforcement points."""

from __future__ import annotations

import asyncio
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
from .async_persistence import (
    AsyncApprovalService,
    AsyncApprovalServiceAdapter,
    AsyncAuthorizationAuditSink,
    AsyncAuthorizationAuditSinkAdapter,
)
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
        runtime_approvals: AsyncApprovalService | None = None,
        runtime_audit_sink: AsyncAuthorizationAuditSink | None = None,
    ) -> None:
        self.provider = provider
        self.approvals = approvals or ApprovalService()
        approval_adapter: AsyncApprovalServiceAdapter | None = None
        if runtime_approvals is None:
            approval_adapter = AsyncApprovalServiceAdapter(self.approvals)
            self.runtime_approvals: AsyncApprovalService = approval_adapter
        else:
            self.runtime_approvals = runtime_approvals
        self._audit_sink = audit_sink
        self._runtime_audit_sink = runtime_audit_sink or (
            None
            if audit_sink is None
            else AsyncAuthorizationAuditSinkAdapter(
                audit_sink,
                offload=None if approval_adapter is None else approval_adapter.offload,
            )
        )
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

        if decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL:
            return await _await_security_completion(
                self._complete_required_approval_decision(
                    action,
                    decision,
                    approval_id=approval_id,
                    risk=risk,
                )
            )

        await self._audit(action, decision, approval_id)
        return decision

    async def _complete_required_approval_decision(
        self,
        action: ProposedAction,
        decision: AuthorizationDecision,
        *,
        approval_id: str | None,
        risk: RiskClassification,
    ) -> AuthorizationDecision:
        """Finish Approval persistence, attention event and audit as one cancellation boundary."""

        resolved_approval = await self.runtime_approvals.resolve_valid_for(
            action,
            approval_id=approval_id,
        )
        if resolved_approval is not None:
            allowed = AuthorizationDecision(
                AuthorizationOutcome.ALLOW,
                reason="exact action covered by approved approval",
                policy_id=decision.policy_id,
                constraints=decision.constraints,
                audit_metadata=decision.audit_metadata,
                adapter_metadata=decision.adapter_metadata,
            )
            await self._audit(action, allowed, resolved_approval.approval_id)
            return allowed

        pending, _ = await self._ensure_pending_and_emit(
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
                "requested_action_digest": action.digest,
            },
            audit_metadata=decision.audit_metadata,
            adapter_metadata=decision.adapter_metadata,
        )
        await self._audit(action, gated, pending.approval_id)
        return gated

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

        record = await self.runtime_approvals.get(approval_id)
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
        await self._audit(action, decision, approval_id)
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
        return await _await_security_completion(
            self._mutate_approval_and_emit(
                self.runtime_approvals.decide_authorized(
                    approval_id,
                    approver_ref=approver.actor_id,
                    approve=approve,
                    comment=comment,
                )
            )
        )

    async def cancel_approval(
        self,
        approval_id: str,
        *,
        actor: ActorIdentity,
        operation: OperationContext,
    ) -> ApprovalRecord:
        """Cancel a pending request as requester or as an authorized approver."""

        record = await self.runtime_approvals.get(approval_id)
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
        await self._audit(action, decision, approval_id)
        if decision.outcome is not AuthorizationOutcome.ALLOW:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                decision.reason or "actor cannot cancel this approval",
                provider_id=self.provider.descriptor.provider_id,
                details={"approval_id": approval_id},
            )
        return await _await_security_completion(
            self._mutate_approval_and_emit(
                self.runtime_approvals.cancel_authorized(
                    approval_id,
                    actor_ref=actor.actor_id,
                )
            )
        )

    def ensure_pending_approval(
        self,
        action: ProposedAction,
        *,
        reason: str,
        policy_id: str,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> ApprovalRecord:
        """Synchronous setup/offline compatibility seam; async callers use the runtime facade."""

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

        record, _ = await _await_security_completion(
            self._ensure_pending_and_emit(
                action,
                reason=reason,
                policy_id=policy_id,
                risk=risk,
            )
        )
        return record

    async def _ensure_pending_and_emit(
        self,
        action: ProposedAction,
        *,
        reason: str,
        policy_id: str,
        risk: RiskClassification,
    ) -> tuple[ApprovalRecord, bool]:
        record, created = await self.runtime_approvals.ensure_pending(
            action,
            reason=reason,
            policy_id=policy_id,
            risk=risk,
        )
        if created:
            await self._emit_approval("required", record)
        return record, created

    async def _mutate_approval_and_emit(
        self,
        mutation: Awaitable[ApprovalRecord],
    ) -> ApprovalRecord:
        updated = await mutation
        await self._emit_approval("resolved", updated)
        return updated

    async def _emit_approval(self, event: str, record: ApprovalRecord) -> None:
        for sink in tuple(self._approval_event_sinks):
            try:
                await sink(event, record)
            # error-boundary: allow-broad-catch=boundary reviewed owner containment boundary
            except Exception:
                # Approval state is authoritative and may already be committed. A downstream
                # attention observer must never turn that successful state transition into a
                # false authorization/approval failure.
                continue

    async def _audit(
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
        if self._runtime_audit_sink is not None:
            await self._runtime_audit_sink.append(record)


async def _await_security_completion[T](operation: Awaitable[T]) -> T:
    """Defer caller cancellation until a security mutation's required side effects finish."""

    task = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        failure = task.exception()
        if failure is not None:
            raise failure from None
        raise


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
