from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from ai_multi_agent_platform.automation import (
    AutomationRuntime,
    AutomationRuntimeTick,
    AutomationService,
    AutomationState,
    DeliveryStatus,
    IdentityContext,
    InMemoryAutomationRepository,
    InMemoryAutomationRuntimeState,
    ReferenceScheduler,
    RetryPolicy,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:issue-241-lifecycle",
        owner_type="user",
        owner_id="issue-241-lifecycle",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(
        title="Retry lifecycle",
        objective="Verify lifecycle suppression for issue 241 retries",
    )


def test_disable_suppresses_pending_retry_without_consuming_attempt() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 13, 0, tzinfo=UTC)
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ContractError(ErrorCode.UNAVAILABLE, "temporary", retryable=True)
            return new_id("task")

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        service._clock = lambda: now
        automation = await service.create_automation(
            name="disabled retry",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=0),
            now=now,
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="disable-before-retry",
            fired_at=now,
        )
        assert failed.status is DeliveryStatus.FAILED
        assert failed.attempt == 1
        assert failed.next_retry_at == now

        await service.set_state(automation.id, AutomationState.DISABLED, now=now)
        assert await service.next_retry_wakeup() is None
        assert await service.retry_due_deliveries(now=now) == ()
        retained = await service.get_delivery(failed.id)
        assert retained.attempt == 1
        assert retained.next_retry_at == now
        assert calls == 1

    asyncio.run(scenario())


def test_invalid_suppresses_pending_retry_and_revalidation_resumes_same_delivery() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 13, 15, tzinfo=UTC)
        keys: list[str] = []

        async def creator(*args: object) -> str:
            keys.append(str(args[3]))
            if len(keys) == 1:
                raise ContractError(ErrorCode.BACKEND_ERROR, "temporary", retryable=True)
            return new_id("task")

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        service._clock = lambda: now
        automation = await service.create_automation(
            name="invalid retry",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=0),
            now=now,
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="invalid-before-retry",
            fired_at=now,
        )
        await service.invalidate_automation(
            automation.id,
            reason_code="required_reference_invalid",
            now=now,
        )

        assert await service.next_retry_wakeup() is None
        assert await service.retry_due_deliveries(now=now) == ()
        retained = await service.get_delivery(failed.id)
        assert retained.id == failed.id
        assert retained.attempt == 1
        assert len(keys) == 1

        revalidated = await service.revalidate_automation(automation.id, now=now)
        assert revalidated.state is AutomationState.ENABLED
        retried = await service.retry_due_deliveries(now=now)
        assert len(retried) == 1
        succeeded = retried[0]
        assert succeeded.id == failed.id
        assert succeeded.status is DeliveryStatus.SUCCEEDED
        assert succeeded.attempt == 2
        assert keys[0] == keys[1]

    asyncio.run(scenario())


def test_zero_delay_retry_wakeup_uses_polling_floor_instead_of_spin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 13, 30, tzinfo=UTC)

        async def creator(*args: object) -> str:
            return new_id("task")

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )

        class ZeroDueRuntime(AutomationRuntime):
            async def run_once(self, *, now: datetime | None = None) -> AutomationRuntimeTick:
                del now
                return AutomationRuntimeTick(retry_delivery_ids=("retry-due",))

            async def _next_wakeup(self) -> datetime | None:
                return now

        runtime = ZeroDueRuntime(
            service=service,
            scheduler=ReferenceScheduler(service),
            events=InMemoryKernelRepository(),
            state=InMemoryAutomationRuntimeState(),
            poll_interval_seconds=0.25,
            clock=lambda: now,
        )
        observed_timeouts: list[float | None] = []

        async def fake_wait_for(awaitable: Any, timeout: float | None) -> Any:
            observed_timeouts.append(timeout)
            runtime._stop_event.set()
            return await awaitable

        monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
        await runtime._run_loop()

        assert observed_timeouts == [0.25]

    asyncio.run(scenario())
