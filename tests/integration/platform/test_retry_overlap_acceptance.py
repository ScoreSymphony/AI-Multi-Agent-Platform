from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.automation import (
    AutomationService,
    DeliveryStatus,
    IdentityContext,
    InMemoryAutomationRepository,
    OverlapPolicy,
    RetryPolicy,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:issue-241-overlap",
        owner_type="user",
        owner_id="issue-241-overlap",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(
        title="Retry overlap",
        objective="Verify retry interaction with overlap policy",
    )


def test_due_retry_is_suppressed_without_consuming_attempt_while_skip_lock_is_held() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 12, 45, tzinfo=UTC)
        blocker_started = asyncio.Event()
        release_blocker = asyncio.Event()
        events: list[dict[str, JsonValue]] = []

        async def creator(*args: object) -> str:
            delivery = args[1]
            assert hasattr(delivery, "dedupe_key")
            dedupe_key = str(delivery.dedupe_key)
            attempt = int(delivery.attempt)
            if dedupe_key == "manual:retry-candidate" and attempt == 1:
                raise ContractError(ErrorCode.BACKEND_ERROR, "temporary", retryable=True)
            if dedupe_key == "manual:blocker":
                blocker_started.set()
                await release_blocker.wait()
            return new_id("task")

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            event_sink=sink,
        )
        service._clock = lambda: now
        automation = await service.create_automation(
            name="skip overlap retry",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=0),
            overlap_policy=OverlapPolicy.SKIP_WHILE_PROCESSING,
            now=now,
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="retry-candidate",
            fired_at=now,
        )
        assert failed.status is DeliveryStatus.FAILED
        assert failed.attempt == 1
        assert failed.next_retry_at == now

        blocker_task = asyncio.create_task(
            service.test_trigger(
                automation.id,
                occurrence_id="blocker",
                fired_at=now,
            )
        )
        await blocker_started.wait()

        suppressed = await service.retry_due_deliveries(now=now)
        assert len(suppressed) == 1
        retained = suppressed[0]
        assert retained.id == failed.id
        assert retained.status is DeliveryStatus.FAILED
        assert retained.attempt == 1
        assert retained.next_retry_at == now
        assert any(
            event.get("type") == "automation.delivery"
            and event.get("trigger_delivery_id") == failed.id
            and event.get("outcome") == "retry-suppressed-overlap"
            for event in events
        )

        release_blocker.set()
        blocker = await blocker_task
        assert blocker.status is DeliveryStatus.SUCCEEDED

        retried = await service.retry_due_deliveries(now=now)
        assert len(retried) == 1
        succeeded = retried[0]
        assert succeeded.id == failed.id
        assert succeeded.status is DeliveryStatus.SUCCEEDED
        assert succeeded.attempt == 2

    asyncio.run(scenario())


def test_due_retry_may_run_while_another_delivery_is_processing_when_overlap_is_allowed() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 13, 0, tzinfo=UTC)
        blocker_started = asyncio.Event()
        release_blocker = asyncio.Event()

        async def creator(*args: object) -> str:
            delivery = args[1]
            assert hasattr(delivery, "dedupe_key")
            dedupe_key = str(delivery.dedupe_key)
            attempt = int(delivery.attempt)
            if dedupe_key == "manual:retry-candidate" and attempt == 1:
                raise ContractError(ErrorCode.UNAVAILABLE, "temporary", retryable=True)
            if dedupe_key == "manual:blocker":
                blocker_started.set()
                await release_blocker.wait()
            return new_id("task")

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        service._clock = lambda: now
        automation = await service.create_automation(
            name="allow overlap retry",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=0),
            overlap_policy=OverlapPolicy.ALLOW,
            now=now,
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="retry-candidate",
            fired_at=now,
        )
        assert failed.status is DeliveryStatus.FAILED
        assert failed.attempt == 1

        blocker_task = asyncio.create_task(
            service.test_trigger(
                automation.id,
                occurrence_id="blocker",
                fired_at=now,
            )
        )
        await blocker_started.wait()

        retried = await service.retry_due_deliveries(now=now)
        assert len(retried) == 1
        succeeded = retried[0]
        assert succeeded.id == failed.id
        assert succeeded.status is DeliveryStatus.SUCCEEDED
        assert succeeded.attempt == 2
        assert not blocker_task.done()

        release_blocker.set()
        blocker = await blocker_task
        assert blocker.status is DeliveryStatus.SUCCEEDED

    asyncio.run(scenario())
