from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_multi_agent_platform.automation import (
    AutomationService,
    AutomationState,
    DeliveryStatus,
    IdentityContext,
    InMemoryAutomationRepository,
    MissedSchedulePolicy,
    OverlapPolicy,
    RetryPolicy,
    SqliteAutomationRepository,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:test",
        owner_type="user",
        owner_id="test",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(
        title="Automated task", objective="Created through canonical task admission"
    )


def _control_plane(*, allowed: bool = True) -> ControlPlane:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=FakeAuthorizationProvider(allowed=allowed),
    )


def test_one_time_schedule_creates_canonical_task_with_provenance() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)
        control_plane = _control_plane()
        automation = await control_plane.automation_service.create_automation(
            name="daily import",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.ONE_TIME, at=now),
            task_template=_template(),
            now=now - timedelta(minutes=1),
        )

        deliveries = await control_plane.automation_scheduler.tick(now=now)

        assert len(deliveries) == 1
        delivery = deliveries[0]
        assert delivery.status is DeliveryStatus.SUCCEEDED
        assert delivery.automation_id == automation.id
        assert delivery.generated_task_id is not None
        task = await control_plane.get_task(
            RequestContext(
                request_id="read",
                correlation_id="read",
                actor=ActorContext(
                    principal_ref="user:test",
                    owner_type="user",
                    owner_id="test",
                ),
            ),
            delivery.generated_task_id,
        )
        assert f"automation:{automation.id}" in task["labels"]
        assert f"delivery:{delivery.id}" in task["labels"]

    asyncio.run(scenario())


def test_recurring_schedule_and_timezone_are_deterministic() -> None:
    async def scenario() -> None:
        berlin = ZoneInfo("Europe/Berlin")
        first = datetime(2026, 10, 25, 8, 0, tzinfo=berlin)
        created = first - timedelta(hours=1)
        calls: list[str] = []

        async def creator(*args: object) -> str:
            calls.append("task")
            return "task_00000000-0000-0000-0000-000000000001"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="recurring",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.RECURRING,
                timezone="Europe/Berlin",
                at=first,
                interval_seconds=3600,
            ),
            task_template=_template(),
            now=created,
        )

        fired = await service.evaluate_due(now=first)
        refreshed = await service.get_automation(automation.id)

        assert len(fired) == 1
        assert calls == ["task"]
        assert refreshed.trigger.timezone == "Europe/Berlin"
        assert refreshed.next_evaluation_at == first.astimezone(UTC) + timedelta(hours=1)

    asyncio.run(scenario())


def test_restart_recovery_coalesces_missed_schedule(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = tmp_path / "automation.sqlite3"
        scheduled = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)
        created = scheduled - timedelta(minutes=5)
        task_ids = iter(
            (
                "task_00000000-0000-0000-0000-000000000010",
                "task_00000000-0000-0000-0000-000000000011",
            )
        )

        async def creator(*args: object) -> str:
            return next(task_ids)

        first = AutomationService(
            repository=SqliteAutomationRepository(db),
            task_creator=creator,
        )
        automation = await first.create_automation(
            name="restart-safe",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.RECURRING,
                at=scheduled,
                interval_seconds=3600,
                missed_schedule_policy=MissedSchedulePolicy.COALESCE,
            ),
            task_template=_template(),
            now=created,
        )

        restarted = AutomationService(
            repository=SqliteAutomationRepository(db),
            task_creator=creator,
        )
        fired = await restarted.evaluate_due(now=scheduled + timedelta(hours=3, minutes=10))
        refreshed = await restarted.get_automation(automation.id)

        assert len(fired) == 1
        assert fired[0].status is DeliveryStatus.SUCCEEDED
        assert refreshed.next_evaluation_at == scheduled + timedelta(hours=4)

    asyncio.run(scenario())


def test_duplicate_webhook_delivery_does_not_duplicate_task() -> None:
    async def scenario() -> None:
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            return "task_00000000-0000-0000-0000-000000000020"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="webhook",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.WEBHOOK,
                webhook_source="github",
                verification_ref="secret://github/webhook",
            ),
            task_template=_template(),
        )
        first = await service.deliver_webhook(
            automation.id,
            event_id="delivery-1",
            payload={"ref": "refs/heads/main"},
            source="github",
            verified=True,
        )
        second = await service.deliver_webhook(
            automation.id,
            event_id="delivery-1",
            payload={"ref": "refs/heads/main"},
            source="github",
            verified=True,
        )

        assert first.id == second.id
        assert first.generated_task_id == second.generated_task_id
        assert calls == 1

    asyncio.run(scenario())


def test_spoofed_webhook_is_rejected_and_queryable() -> None:
    async def scenario() -> None:
        async def creator(*args: object) -> str:
            raise AssertionError("spoofed webhook must not reach task creation")

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="webhook",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.WEBHOOK, webhook_source="github"),
            task_template=_template(),
        )
        try:
            await service.deliver_webhook(
                automation.id,
                event_id="spoofed",
                payload={},
                source="github",
                verified=False,
            )
        except ContractError as exc:
            assert exc.code is ErrorCode.FORBIDDEN
        else:
            raise AssertionError("spoofed webhook must be rejected")

        history = await service.list_deliveries(automation.id)
        assert len(history) == 1
        assert history[0].status is DeliveryStatus.REJECTED
        assert history[0].generated_task_id is None

    asyncio.run(scenario())


def test_platform_event_filter_and_pause_are_enforced() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def creator(*args: object) -> str:
            calls.append("task")
            return "task_00000000-0000-0000-0000-000000000030"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="failed task watcher",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="task.failed",
                filters={"project_id": "project_alpha"},
            ),
            task_template=_template(),
        )
        ignored = await service.deliver_platform_event(
            event_id="event-1",
            event_type="task.failed",
            payload={"project_id": "project_other"},
        )
        matched = await service.deliver_platform_event(
            event_id="event-2",
            event_type="task.failed",
            payload={"project_id": "project_alpha"},
        )
        await service.set_state(automation.id, AutomationState.PAUSED)
        paused = await service.deliver_platform_event(
            event_id="event-3",
            event_type="task.failed",
            payload={"project_id": "project_alpha"},
        )

        assert ignored == ()
        assert len(matched) == 1
        assert paused == ()
        assert calls == ["task"]

    asyncio.run(scenario())


def test_unauthorized_generated_task_fails_inside_normal_task_admission() -> None:
    async def scenario() -> None:
        control_plane = _control_plane(allowed=False)
        automation = await control_plane.automation_service.create_automation(
            name="denied",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
        )

        delivery = await control_plane.automation_service.test_trigger(
            automation.id,
            occurrence_id="denied-1",
        )

        assert delivery.status is DeliveryStatus.FAILED
        assert delivery.error_code == ErrorCode.FORBIDDEN.value
        assert delivery.generated_task_id is None

    asyncio.run(scenario())


def test_schedule_update_increments_revision_and_recomputes_next_fire() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)

        async def creator(*args: object) -> str:
            return "task_00000000-0000-0000-0000-000000000040"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        original = await service.create_automation(
            name="schedule",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.ONE_TIME,
                at=now + timedelta(hours=1),
            ),
            task_template=_template(),
            now=now,
        )
        changed = await service.update_automation(
            original.id,
            trigger=TriggerDefinition(
                type=TriggerType.ONE_TIME,
                at=now + timedelta(hours=2),
            ),
            now=now + timedelta(minutes=1),
        )

        assert changed.revision == original.revision + 1
        assert changed.next_evaluation_at == now + timedelta(hours=2)

    asyncio.run(scenario())


def test_failed_processing_can_retry_same_delivery_without_duplicate_occurrence() -> None:
    async def scenario() -> None:
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ContractError(ErrorCode.UNAVAILABLE, "temporary", retryable=True)
            return "task_00000000-0000-0000-0000-000000000050"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="retry",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
        )
        failed = await service.test_trigger(automation.id, occurrence_id="same-occurrence")
        retried = await service.retry_delivery(failed.id)

        assert failed.status is DeliveryStatus.FAILED
        assert retried.id == failed.id
        assert retried.status is DeliveryStatus.SUCCEEDED
        assert retried.attempt == 2
        assert calls == 2

    asyncio.run(scenario())


def test_overlap_allow_processes_distinct_deliveries_concurrently() -> None:
    async def scenario() -> None:
        active = 0
        peak_active = 0
        task_number = 60
        both_started = asyncio.Event()
        release = asyncio.Event()

        async def creator(*args: object) -> str:
            nonlocal active, peak_active, task_number
            active += 1
            peak_active = max(peak_active, active)
            if active == 2:
                both_started.set()
            await release.wait()
            active -= 1
            task_number += 1
            return f"task_00000000-0000-0000-0000-{task_number:012d}"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="parallel",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            overlap_policy=OverlapPolicy.ALLOW,
        )

        first = asyncio.create_task(
            service.test_trigger(automation.id, occurrence_id="parallel-one")
        )
        second = asyncio.create_task(
            service.test_trigger(automation.id, occurrence_id="parallel-two")
        )
        await asyncio.wait_for(both_started.wait(), timeout=1.0)
        release.set()
        deliveries = await asyncio.gather(first, second)

        assert peak_active == 2
        assert all(item.status is DeliveryStatus.SUCCEEDED for item in deliveries)
        assert deliveries[0].id != deliveries[1].id

    asyncio.run(scenario())


def test_overlap_skip_rejects_second_delivery_while_processing() -> None:
    async def scenario() -> None:
        calls = 0
        first_started = asyncio.Event()
        release = asyncio.Event()

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            first_started.set()
            await release.wait()
            return "task_00000000-0000-0000-0000-000000000070"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="serial",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            overlap_policy=OverlapPolicy.SKIP_WHILE_PROCESSING,
        )

        first_task = asyncio.create_task(
            service.test_trigger(automation.id, occurrence_id="serial-one")
        )
        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        second = await service.test_trigger(automation.id, occurrence_id="serial-two")
        release.set()
        first = await first_task

        assert first.status is DeliveryStatus.SUCCEEDED
        assert second.status is DeliveryStatus.REJECTED
        assert second.error_code == "overlap_skipped"
        assert calls == 1

    asyncio.run(scenario())


def test_webhook_admission_rejects_oversized_payload_before_processing() -> None:
    async def scenario() -> None:
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            return "task_00000000-0000-0000-0000-000000000080"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            max_webhook_payload_bytes=32,
        )
        automation = await service.create_automation(
            name="bounded webhook",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.WEBHOOK, webhook_source="github"),
            task_template=_template(),
        )

        try:
            await service.deliver_webhook(
                automation.id,
                event_id="delivery-too-large",
                payload={"body": "x" * 64},
                source="github",
                verified=True,
            )
        except ContractError as exc:
            assert exc.code is ErrorCode.INPUT_TOO_LARGE
            assert exc.details["max_bytes"] == 32
        else:
            raise AssertionError("oversized webhook must be rejected")

        assert calls == 0
        assert await service.list_deliveries(automation.id) == ()

    asyncio.run(scenario())


def test_retry_and_overlap_policies_are_create_and_update_configuration() -> None:
    async def scenario() -> None:
        async def creator(*args: object) -> str:
            return "task_00000000-0000-0000-0000-000000000090"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        created = await service.create_automation(
            name="policy",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=5, base_backoff_seconds=2.5),
            overlap_policy=OverlapPolicy.ALLOW,
        )
        updated = await service.update_automation(
            created.id,
            retry_policy=RetryPolicy(max_attempts=2, base_backoff_seconds=0.5),
            overlap_policy=OverlapPolicy.SKIP_WHILE_PROCESSING,
        )

        assert created.retry_policy.max_attempts == 5
        assert created.overlap_policy is OverlapPolicy.ALLOW
        assert updated.retry_policy.max_attempts == 2
        assert updated.retry_policy.base_backoff_seconds == 0.5
        assert updated.overlap_policy is OverlapPolicy.SKIP_WHILE_PROCESSING
        assert updated.revision == created.revision + 1

    asyncio.run(scenario())
