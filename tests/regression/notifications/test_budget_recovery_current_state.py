from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.accounting import (
    BudgetAction,
    BudgetState,
    ThresholdLevel,
    UsageBudget,
)
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import NotificationQuery, RecipientRef, RecipientType
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


class _RevisedBudgetAccounting:
    def __init__(self, listed: UsageBudget, current: UsageBudget) -> None:
        self.listed = listed
        self.current = current
        self.state_reads = 0

    async def list_budgets(self) -> tuple[UsageBudget, ...]:
        return (self.listed,)

    async def budget_state(self, budget_id: str) -> BudgetState:
        assert budget_id == self.current.id
        self.state_reads += 1
        return BudgetState(
            budget=self.current,
            consumed=90.0,
            remaining=10.0,
            fraction=0.9,
            level=ThresholdLevel.WARNING,
        )

    async def get_threshold_generation(self, budget_id: str) -> int:
        assert budget_id == self.current.id
        return 1


def test_budget_recovery_uses_confirmed_current_owner_and_scope(tmp_path) -> None:
    async def scenario() -> None:
        stale_recipient = RecipientRef(RecipientType.USER, new_id("user"))
        current_recipient = RecipientRef(RecipientType.USER, new_id("user"))
        stale_project = new_id("project")
        current_project = new_id("project")
        listed = UsageBudget(
            metric_type="storage.bytes",
            unit="bytes",
            scope_type="project",
            scope_id=stale_project,
            limit=100.0,
            warning_fraction=0.8,
            action=BudgetAction.NOTIFY,
            owner_type=stale_recipient.type.value,
            owner_id=stale_recipient.id,
        )
        current = replace(
            listed,
            scope_id=current_project,
            owner_id=current_recipient.id,
            version=2,
        )
        accounting = _RevisedBudgetAccounting(listed, current)
        kernel, events = _kernel()
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            notification_state_path=tmp_path / "notifications.sqlite3",
        )

        recovered, complete = await control_plane._recover_persisted_budget_thresholds(
            accounting,  # type: ignore[arg-type]
            now=None,
        )

        stale_history = await control_plane.notification_service.list(
            NotificationQuery(recipient=stale_recipient, include_archived=True)
        )
        current_history = await control_plane.notification_service.list(
            NotificationQuery(recipient=current_recipient, include_archived=True)
        )

        assert complete is True
        assert accounting.state_reads == 2
        assert stale_history == ()
        assert len(recovered) == len(current_history) == 1
        assert current_history[0].summary["scope_id"] == current_project
        assert current_history[0].summary["budget_version"] == 2

    asyncio.run(scenario())
