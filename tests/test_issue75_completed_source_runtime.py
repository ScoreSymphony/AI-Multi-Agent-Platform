from __future__ import annotations

import asyncio

from ai_multi_agent_platform.accounting import (
    AccountingService,
    BudgetAction,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageBudget,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    OperationContext,
)
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import (
    NotificationCategory,
    NotificationQuery,
    RecipientRef,
    RecipientType,
)
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class _ApprovalAuthorization(FakeAuthorizationProvider):
    async def authorize(self, request):
        self.calls.append(request)
        if request.action == AuthorizationAction.APPROVE.value:
            return AuthorizationDecision(AuthorizationOutcome.ALLOW, reason="approver")
        return AuthorizationDecision(
            AuthorizationOutcome.REQUIRE_APPROVAL,
            reason="review required",
            policy_id="issue75-approval",
        )


def _kernel() -> tuple[PlatformKernel, InMemoryKernelRepository]:
    repository = InMemoryKernelRepository()
    return (
        PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        repository,
    )


def test_authorization_gate_required_and_resolved_events_project_into_notifications() -> None:
    async def scenario() -> None:
        provider = _ApprovalAuthorization()
        gate = AuthorizationGate(provider)
        kernel, events = _kernel()
        approver = RecipientRef(RecipientType.USER, new_id("user"))

        async def recipients(event, approval):
            del event, approval
            return (approver,)

        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            authorization=provider,
            approval_gate=gate,
            approval_recipient_resolver=recipients,
        )
        task_id = new_id("task")
        action = ProposedAction(
            AuthorizationContext(
                actor=ActorIdentity(new_id("user"), ActorType.HUMAN),
                action=AuthorizationAction.EXECUTE,
                resource_type=ResourceType.TASK,
                resource_id=task_id,
                operation=OperationContext(),
                task_id=task_id,
            ),
            payload={"operation": "issue75-fixture"},
        )

        decision = await gate.decide(action)
        approval_id = decision.constraints["approval_id"]
        assert isinstance(approval_id, str)
        pending = await control_plane.notification_service.list(
            NotificationQuery(recipient=approver)
        )

        assert len(pending) == 1
        assert pending[0].category is NotificationCategory.APPROVAL
        assert pending[0].summary["approval_id"] == approval_id
        assert pending[0].summary["action"] == AuthorizationAction.EXECUTE.value

        await gate.decide_approval(
            approval_id,
            approver=ActorIdentity(approver.id, ActorType.HUMAN),
            approve=True,
            operation=OperationContext(),
        )
        projected = await control_plane.notification_service.list(
            NotificationQuery(recipient=approver)
        )

        assert len(projected) == 2
        assert {item.summary.get("status") for item in projected} == {None, "approved"}

    asyncio.run(scenario())


def test_accounting_threshold_sink_is_drained_by_autonomous_notification_runtime() -> None:
    async def scenario() -> None:
        accounting = AccountingService(InMemoryUsageStore())
        kernel, events = _kernel()
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            accounting_service=accounting,
        )
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        project_id = new_id("project")
        budget = UsageBudget(
            metric_type="storage.bytes",
            unit="bytes",
            scope_type="project",
            scope_id=project_id,
            limit=100.0,
            warning_fraction=0.8,
            action=BudgetAction.NOTIFY,
            owner_type=recipient.type.value,
            owner_id=recipient.id,
        )
        accounting.put_budget(budget)

        accounting.record(
            UsageRecord(
                metric_type="storage.bytes",
                unit="bytes",
                quality=MeasurementQuality.MEASURED,
                source="storage-provider",
                quantity=80.0,
                scope=UsageScope(project_id=project_id),
            )
        )
        before_tick = await control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )
        tick = await control_plane.run_notification_runtime_once()
        after_tick = await control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )

        assert before_tick == ()
        assert tick.reminder_notifications == 1
        assert len(after_tick) == 1
        assert after_tick[0].category is NotificationCategory.RESOURCE
        assert after_tick[0].summary["budget_id"] == budget.id
        assert after_tick[0].summary["level"] == "warning"

    asyncio.run(scenario())


def test_notification_observer_failure_does_not_fail_authoritative_approval_transition() -> None:
    async def scenario() -> None:
        provider = _ApprovalAuthorization()
        gate = AuthorizationGate(provider)

        async def broken_sink(event, record):
            del event, record
            raise RuntimeError("notification backend unavailable")

        gate.add_approval_event_sink(broken_sink)
        task_id = new_id("task")
        action = ProposedAction(
            AuthorizationContext(
                actor=ActorIdentity(new_id("user"), ActorType.HUMAN),
                action=AuthorizationAction.EXECUTE,
                resource_type=ResourceType.TASK,
                resource_id=task_id,
                operation=OperationContext(),
                task_id=task_id,
            ),
            payload={},
        )

        decision = await gate.decide(action)
        approval_id = decision.constraints["approval_id"]
        assert isinstance(approval_id, str)
        resolved = await gate.decide_approval(
            approval_id,
            approver=ActorIdentity(new_id("user"), ActorType.HUMAN),
            approve=True,
            operation=OperationContext(),
        )

        assert resolved.status.value == "approved"

    asyncio.run(scenario())
