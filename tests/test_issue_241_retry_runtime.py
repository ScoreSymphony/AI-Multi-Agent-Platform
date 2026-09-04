from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.automation import (
    AutomationRuntime,
    AutomationService,
    AutomationState,
    DeliveryStatus,
    IdentityContext,
    InMemoryAutomationRepository,
    InMemoryAutomationRuntimeState,
    ReferenceScheduler,
    RetryPolicy,
    SqliteAutomationRepository,
    SqliteAutomationRuntimeState,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.automation.service import AutomationService as BaseAutomationService
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:issue-241",
        owner_type="user",
        owner_id="issue-241",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(
        title="Retry task",
        objective="Verify durable Automation retry semantics",
    )


def _manual_trigger() -> TriggerDefinition:
    return TriggerDefinition(type=TriggerType.MANUAL)


def test_retryable_failure_gets_deterministic_deadline_and_retries_when_due() -> None:
    async def scenario() -> None:
        now = [datetime(2026, 9, 4, 2, 0, tzinfo=UTC)]
        idempotency_keys: list[str] = []
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            idempotency_keys.append(cast(str, args[3]))
            if calls == 1:
                raise ContractError(ErrorCode.UNAVAILABLE, "temporary", retryable=True)
            return new_id("task")

        service = BaseAutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            clock=lambda: now[0],
        )
        automation = await service.create_automation(
            name="retryable",
            description="",
            identity=_identity(),
            trigger=_manual_trigger(),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=10),
            now=now[0],
        )

        failed = await service.test_trigger(
            automation.id,
            occurrence_id="one",
            fired_at=now[0],
        )
        assert failed.status is DeliveryStatus.FAILED
        assert failed.attempt == 1
        assert failed.retryable is True
        assert failed.last_failed_at == now[0]
        assert failed.next_retry_at == now[0] + timedelta(seconds=10)
        assert failed.retry_exhausted_at is None

        now[0] += timedelta(seconds=9)
        assert await service.retry_due_deliveries(now=now[0]) == ()

        now[0] += timedelta(seconds=1)
        retried = await service.retry_due_deliveries(now=now[0])
        assert len(retried) == 1
        succeeded = retried[0]
        assert succeeded.id == failed.id
        assert succeeded.status is DeliveryStatus.SUCCEEDED
        assert succeeded.attempt == 2
        assert succeeded.next_retry_at is None
        assert succeeded.retry_exhausted_at is None
        assert calls == 2
        assert idempotency_keys[0] == idempotency_keys[1]

    asyncio.run(scenario())


def test_sqlite_retry_deadline_survives_restart_and_runtime_executes_same_delivery(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "automation-retry.sqlite3"
        failed_at = datetime(2026, 9, 4, 2, 15, tzinfo=UTC)

        async def failing_creator(*args: object) -> str:
            raise ContractError(ErrorCode.TIMEOUT, "temporary timeout", retryable=True)

        repository = SqliteAutomationRepository(db_path)
        service = BaseAutomationService(
            repository=repository,
            task_creator=failing_creator,
            clock=lambda: failed_at,
        )
        automation = await service.create_automation(
            name="restart-safe",
            description="",
            identity=_identity(),
            trigger=_manual_trigger(),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=5),
            now=failed_at,
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="restart",
            fired_at=failed_at,
        )
        assert failed.next_retry_at == failed_at + timedelta(seconds=5)

        created_keys: list[str] = []

        async def succeeding_creator(*args: object) -> str:
            created_keys.append(cast(str, args[3]))
            return new_id("task")

        restarted_repository = SqliteAutomationRepository(db_path)
        restarted_service = BaseAutomationService(
            repository=restarted_repository,
            task_creator=succeeding_creator,
            clock=lambda: failed_at + timedelta(seconds=5),
        )
        restored = await restarted_service.get_delivery(failed.id)
        assert restored.id == failed.id
        assert restored.attempt == 1
        assert restored.retryable is True
        assert restored.next_retry_at == failed.next_retry_at

        runtime = AutomationRuntime(
            service=cast(AutomationService, restarted_service),
            scheduler=ReferenceScheduler(restarted_service),
            events=InMemoryKernelRepository(),
            state=SqliteAutomationRuntimeState(db_path),
            clock=lambda: failed_at + timedelta(seconds=5),
        )
        tick = await runtime.run_once(now=failed_at + timedelta(seconds=5))
        succeeded = await restarted_service.get_delivery(failed.id)

        assert tick.retry_delivery_ids == (failed.id,)
        assert succeeded.status is DeliveryStatus.SUCCEEDED
        assert succeeded.id == failed.id
        assert succeeded.attempt == 2
        assert len(created_keys) == 1

    asyncio.run(scenario())


def test_retry_exhaustion_is_persisted_and_not_scheduled_again() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 2, 30, tzinfo=UTC)
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            raise ContractError(ErrorCode.TRANSIENT_FAILURE, "still failing", retryable=True)

        service = BaseAutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            clock=lambda: now,
        )
        automation = await service.create_automation(
            name="exhaustion",
            description="",
            identity=_identity(),
            trigger=_manual_trigger(),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=2, base_backoff_seconds=0),
            now=now,
        )
        first = await service.test_trigger(
            automation.id,
            occurrence_id="exhaust",
            fired_at=now,
        )
        assert first.next_retry_at == now

        retried = await service.retry_due_deliveries(now=now)
        exhausted = retried[0]
        assert exhausted.status is DeliveryStatus.FAILED
        assert exhausted.attempt == 2
        assert exhausted.retryable is True
        assert exhausted.next_retry_at is None
        assert exhausted.retry_exhausted_at == now
        assert await service.next_retry_wakeup() is None

        manual = await service.retry_delivery(exhausted.id)
        assert manual.attempt == 2
        assert calls == 2

    asyncio.run(scenario())


def test_pause_suppresses_pending_retry_until_automation_is_enabled_again() -> None:
    async def scenario() -> None:
        now = [datetime(2026, 9, 4, 2, 45, tzinfo=UTC)]
        calls = 0
        events: list[dict[str, object]] = []

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ContractError(ErrorCode.UNAVAILABLE, "temporary", retryable=True)
            return new_id("task")

        async def sink(event: dict[str, object]) -> None:
            events.append(event)

        service = BaseAutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            event_sink=cast(object, sink),
            clock=lambda: now[0],
        )
        automation = await service.create_automation(
            name="pause retry",
            description="",
            identity=_identity(),
            trigger=_manual_trigger(),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=5),
            now=now[0],
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="pause",
            fired_at=now[0],
        )
        await service.set_state(automation.id, AutomationState.PAUSED, now=now[0])
        now[0] += timedelta(seconds=10)

        assert await service.next_retry_wakeup() is None
        assert await service.retry_due_deliveries(now=now[0]) == ()
        suppressed = await service.retry_delivery(failed.id)
        assert suppressed.attempt == 1
        assert calls == 1
        assert any(event.get("outcome") == "retry-suppressed" for event in events)

        await service.set_state(automation.id, AutomationState.ENABLED, now=now[0])
        assert await service.next_retry_wakeup() == failed.next_retry_at
        retried = await service.retry_due_deliveries(now=now[0])
        assert retried[0].status is DeliveryStatus.SUCCEEDED
        assert calls == 2

    asyncio.run(scenario())


def test_manual_retry_consumes_pending_automatic_retry_without_duplicate_task() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 3, 0, tzinfo=UTC)
        keys: list[str] = []

        async def creator(*args: object) -> str:
            keys.append(cast(str, args[3]))
            if len(keys) == 1:
                raise ContractError(ErrorCode.UNAVAILABLE, "temporary", retryable=True)
            return new_id("task")

        service = BaseAutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            clock=lambda: now,
        )
        automation = await service.create_automation(
            name="manual wins",
            description="",
            identity=_identity(),
            trigger=_manual_trigger(),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=60),
            now=now,
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="manual",
            fired_at=now,
        )
        assert failed.next_retry_at == now + timedelta(seconds=60)

        manual = await service.retry_delivery(failed.id)
        assert manual.id == failed.id
        assert manual.status is DeliveryStatus.SUCCEEDED
        assert manual.attempt == 2
        assert manual.next_retry_at is None
        assert keys[0] == keys[1]

        assert await service.retry_due_deliveries(now=now + timedelta(minutes=5)) == ()
        assert len(keys) == 2

    asyncio.run(scenario())


def test_terminal_failure_is_not_automatically_scheduled() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 3, 15, tzinfo=UTC)

        async def creator(*args: object) -> str:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "configuration is permanently invalid",
            )

        service = BaseAutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            clock=lambda: now,
        )
        automation = await service.create_automation(
            name="terminal",
            description="",
            identity=_identity(),
            trigger=_manual_trigger(),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=1),
            now=now,
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="terminal",
            fired_at=now,
        )

        assert failed.status is DeliveryStatus.FAILED
        assert failed.retryable is False
        assert failed.next_retry_at is None
        assert failed.retry_exhausted_at is None
        assert await service.retry_due_deliveries(now=now + timedelta(days=1)) == ()

    asyncio.run(scenario())


def test_concurrent_runtime_ticks_do_not_duplicate_same_retry_processing() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 3, 30, tzinfo=UTC)
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ContractError(ErrorCode.UNAVAILABLE, "temporary", retryable=True)
            await asyncio.sleep(0.01)
            return new_id("task")

        service = BaseAutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            clock=lambda: now,
        )
        automation = await service.create_automation(
            name="concurrent retry",
            description="",
            identity=_identity(),
            trigger=_manual_trigger(),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=0),
            now=now,
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="concurrent",
            fired_at=now,
        )
        runtime = AutomationRuntime(
            service=cast(AutomationService, service),
            scheduler=ReferenceScheduler(service),
            events=InMemoryKernelRepository(),
            state=InMemoryAutomationRuntimeState(),
            clock=lambda: now,
        )

        await asyncio.gather(
            runtime.run_once(now=now),
            runtime.run_once(now=now),
        )
        final = await service.get_delivery(failed.id)

        assert final.status is DeliveryStatus.SUCCEEDED
        assert final.attempt == 2
        assert calls == 2

    asyncio.run(scenario())
