from __future__ import annotations

import asyncio

from ai_multi_agent_platform.accounting import (
    AccountingService,
    BudgetAction,
    MeasurementQuality,
    SQLiteUsageStore,
    ThresholdLevel,
    UsageBudget,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import (
    NotificationCategory,
    NotificationQuery,
    NotificationState,
    RecipientRef,
    RecipientType,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


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


def test_persisted_threshold_recovers_notification_after_process_restart(tmp_path) -> None:
    async def scenario() -> None:
        accounting_path = tmp_path / "accounting.sqlite3"
        notification_path = tmp_path / "notifications.sqlite3"
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        project_id = new_id("project")

        # Simulate the crash window: #76 commits usage + threshold state, but no
        # Notification Control Plane exists to receive the synchronous observer callback.
        first = AccountingService(SQLiteUsageStore(accounting_path))
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
        first.put_budget(budget)
        first.record(
            UsageRecord(
                metric_type="storage.bytes",
                unit="bytes",
                quality=MeasurementQuality.MEASURED,
                source="storage-provider",
                quantity=80.0,
                scope=UsageScope(project_id=project_id),
            )
        )
        assert first.store.get_threshold_level(budget.id) is ThresholdLevel.WARNING

        # A new process receives no replayed synchronous #76 callback. The first
        # Notification runtime tick must reconstruct attention from durable accounting state.
        restarted_accounting = AccountingService(SQLiteUsageStore(accounting_path))
        kernel, events = _kernel()
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            accounting_service=restarted_accounting,
            notification_state_path=notification_path,
        )

        before = await control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )
        tick = await control_plane.run_notification_runtime_once()
        recovered = await control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )

        assert before == ()
        assert tick.reminder_notifications == 1
        assert len(recovered) == 1
        assert recovered[0].category is NotificationCategory.RESOURCE
        assert recovered[0].summary["budget_id"] == budget.id
        assert recovered[0].summary["level"] == ThresholdLevel.WARNING.value

        # Recovery is historical/idempotent, not a per-tick poll notification. Even a
        # dismissed item must not be resurrected simply because the process restarts.
        await control_plane.notification_service.dismiss(recovered[0].id, recipient=recipient)
        second_accounting = AccountingService(SQLiteUsageStore(accounting_path))
        second_kernel, second_events = _kernel()
        restarted_control_plane = ControlPlane(
            kernel=second_kernel,
            events=second_events,
            accounting_service=second_accounting,
            notification_state_path=notification_path,
        )
        second_tick = await restarted_control_plane.run_notification_runtime_once()
        history = await restarted_control_plane.notification_service.list(
            NotificationQuery(recipient=recipient, include_archived=True)
        )

        assert second_tick.reminder_notifications == 0
        assert len(history) == 1
        assert history[0].state is NotificationState.DISMISSED

    asyncio.run(scenario())
