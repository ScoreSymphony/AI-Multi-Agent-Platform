"""Cancellation completion regressions for issue #892 Approval side effects."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import AuthorizationOutcome, OperationContext
from ai_multi_agent_platform.domain import ApprovalStatus, new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
    SqliteApprovalService,
    SqliteAuthorizationAuditSink,
)


class _BlockingApprovalService(SqliteApprovalService):
    def __init__(self, path: Path) -> None:
        self.block_connections = False
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)

    def arm(self) -> None:
        self.started.clear()
        self.release.clear()
        self.block_connections = True

    def _connect(self) -> sqlite3.Connection:
        if self.block_connections:
            self.started.set()
            if not self.release.wait(timeout=3):
                raise TimeoutError("test Approval persistence release timed out")
        return super()._connect()


def _provider() -> LocalAuthorizationProvider:
    return LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="agent:runtime",
                actor_types=frozenset({ActorType.AGENT}),
                approval_actions=frozenset({AuthorizationAction.EXECUTE}),
                resource_types=frozenset({ResourceType.RUN}),
            ),
            LocalPrincipalPolicy(
                principal_ref="user:reviewer",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                resource_types=frozenset({ResourceType.RUN}),
            ),
        )
    )


def _action() -> ProposedAction:
    return ProposedAction(
        AuthorizationContext(
            actor=ActorIdentity("agent:runtime", ActorType.AGENT),
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.RUN,
            resource_id=new_id("run"),
            operation=OperationContext(
                correlation_id="corr-issue-892-cancellation-completion",
                owner_type="agent",
                owner_id="runtime",
            ),
        ),
        payload={"operation": "sensitive"},
    )


def test_cancelled_gate_decision_finishes_required_event_and_audit(tmp_path: Path) -> None:
    async def scenario() -> None:
        approvals_path = tmp_path / "approvals.sqlite3"
        audit_path = tmp_path / "authorization-audit.sqlite3"
        approvals = _BlockingApprovalService(approvals_path)
        audit = SqliteAuthorizationAuditSink(audit_path)
        events: list[tuple[str, str]] = []

        async def record_event(event: str, record: object) -> None:
            events.append((event, getattr(record, "approval_id")))

        gate = AuthorizationGate(
            _provider(),
            approvals=approvals,
            audit_sink=audit,
            approval_event_sink=record_event,
        )
        action = _action()
        approvals.arm()
        pending = asyncio.create_task(gate.decide(action))
        assert await asyncio.to_thread(approvals.started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()

        approvals.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

        persisted = SqliteApprovalService(approvals_path).all()
        assert len(persisted) == 1
        record = persisted[0]
        assert record.status is ApprovalStatus.PENDING
        assert events == [("required", record.approval_id)]

        in_memory_audit = gate.audit_records
        durable_audit = audit.all()
        assert len(in_memory_audit) == 1
        assert len(durable_audit) == 1
        assert durable_audit[0] == in_memory_audit[0]
        assert durable_audit[0].outcome is AuthorizationOutcome.REQUIRE_APPROVAL
        assert durable_audit[0].approval_id == record.approval_id
        assert durable_audit[0].requested_action_digest == action.digest

    asyncio.run(scenario())


def test_cancelled_approval_resolution_still_emits_resolved_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        approvals_path = tmp_path / "approvals.sqlite3"
        approvals = _BlockingApprovalService(approvals_path)
        action = _action()
        pending_record = approvals.request(
            action,
            reason="review required",
            policy_id="policy:test",
        )
        events: list[tuple[str, str]] = []

        async def record_event(event: str, record: object) -> None:
            events.append((event, getattr(record, "approval_id")))

        gate = AuthorizationGate(
            _provider(),
            approvals=approvals,
            approval_event_sink=record_event,
        )
        approvals.arm()
        resolution = asyncio.create_task(
            gate.decide_approval(
                pending_record.approval_id,
                approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
                approve=True,
                operation=OperationContext(
                    correlation_id="corr-issue-892-resolution",
                    owner_type="user",
                    owner_id="reviewer",
                ),
            )
        )
        assert await asyncio.to_thread(approvals.started.wait, 1)

        resolution.cancel()
        await asyncio.sleep(0)
        resolution.cancel()
        assert not resolution.done()

        approvals.release.set()
        with pytest.raises(asyncio.CancelledError):
            await resolution

        persisted = SqliteApprovalService(approvals_path).get(pending_record.approval_id)
        assert persisted.status is ApprovalStatus.APPROVED
        assert events == [("resolved", pending_record.approval_id)]

    asyncio.run(scenario())
