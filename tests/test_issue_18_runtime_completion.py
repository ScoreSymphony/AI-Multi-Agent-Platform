from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.automation import (
    DeliveryStatus,
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneASGI,
    ControlPlaneHTTP,
    RequestContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:runtime-owner",
        owner_type="user",
        owner_id="runtime-owner",
    )


def _template(*, project_id: str | None = None) -> TaskTemplate:
    return TaskTemplate(
        title="Runtime generated task",
        objective="Prove the completed Automation runtime path",
        project_id=project_id,
    )


def _stack(
    *,
    events: InMemoryKernelRepository | None = None,
    state_path: Path | None = None,
    poll_seconds: float = 0.01,
) -> tuple[ControlPlane, PlatformKernel, InMemoryKernelRepository]:
    repository = events or InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=FakeAuthorizationProvider(),
        automation_state_path=state_path,
        automation_runtime_poll_seconds=poll_seconds,
    )
    return control_plane, kernel, repository


def _context(
    *,
    key: str,
    principal_ref: str = "user:runtime-owner",
    owner_type: str = "user",
    owner_id: str = "runtime-owner",
    actor_type: str | None = None,
) -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        actor=ActorContext(
            principal_ref=principal_ref,
            owner_type=owner_type,  # type: ignore[arg-type]
            owner_id=owner_id,
            actor_type=actor_type,
        ),
        idempotency_key=key,
    )


def test_autonomous_reference_scheduler_fires_and_durable_state_survives_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        state_path = tmp_path / "automation-runtime.sqlite3"
        control_plane, _kernel, events = _stack(state_path=state_path)
        now = datetime.now(UTC)
        automation = await control_plane.automation_service.create_automation(
            name="autonomous one-time",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.ONE_TIME,
                at=now + timedelta(milliseconds=50),
            ),
            task_template=_template(),
            now=now,
        )

        await control_plane.start_automation_runtime()
        try:
            for _ in range(100):
                deliveries = await control_plane.automation_service.list_deliveries(automation.id)
                if deliveries:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("autonomous scheduler did not fire the due Automation")
        finally:
            await control_plane.stop_automation_runtime()

        assert len(deliveries) == 1
        assert deliveries[0].status is DeliveryStatus.SUCCEEDED
        assert deliveries[0].generated_task_id is not None

        restarted, _kernel2, _events2 = _stack(events=events, state_path=state_path)
        persisted = await restarted.automation_service.get_automation(automation.id)
        persisted_deliveries = await restarted.automation_service.list_deliveries(automation.id)
        assert persisted.id == automation.id
        assert len(persisted_deliveries) == 1
        assert persisted.next_evaluation_at is None

    asyncio.run(scenario())


def test_runtime_consumes_canonical_kernel_events_and_persists_event_cursor(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_path = tmp_path / "event-runtime.sqlite3"
        control_plane, kernel, events = _stack(state_path=state_path)
        project_id = new_id("project")
        automation = await control_plane.automation_service.create_automation(
            name="canonical task event watcher",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="task.created",
                filters={"project_id": project_id},
            ),
            task_template=_template(),
        )

        await kernel.create_task(
            idempotency_key="seed-task",
            title="Seed canonical event",
            objective="Generate a real #6 task.created event",
            owner_type="user",
            owner_id="runtime-owner",
            project_id=project_id,
        )
        first = await control_plane.automation_runtime.run_once()
        deliveries = await control_plane.automation_service.list_deliveries(automation.id)

        assert len(first.processed_event_ids) == 1
        assert len(first.event_delivery_ids) == 1
        assert len(deliveries) == 1
        assert deliveries[0].status is DeliveryStatus.SUCCEEDED
        assert deliveries[0].generated_task_id is not None

        restarted, _kernel2, _events2 = _stack(events=events, state_path=state_path)
        second = await restarted.automation_runtime.run_once()
        restarted_deliveries = await restarted.automation_service.list_deliveries(automation.id)

        assert first.processed_event_ids[0] not in second.processed_event_ids
        assert second.event_delivery_ids == ()
        assert len(restarted_deliveries) == 1

    asyncio.run(scenario())


def test_configuration_command_idempotency_replays_across_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_path = tmp_path / "command-runtime.sqlite3"
        control_plane, _kernel, events = _stack(state_path=state_path)
        automation = await control_plane.automation_service.create_automation(
            name="replayable configuration",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
        )
        context = _context(key="pause-once")

        first = await control_plane.execute_command(
            context,
            "automation.pause",
            automation.id,
            {},
        )
        second = await control_plane.execute_command(
            context,
            "automation.pause",
            automation.id,
            {},
        )
        assert second == first

        revision = first["revision"]
        refreshed = await control_plane.automation_service.get_automation(automation.id)
        assert refreshed.revision == revision

        restarted, _kernel2, _events2 = _stack(events=events, state_path=state_path)
        third = await restarted.execute_command(
            context,
            "automation.pause",
            automation.id,
            {},
        )
        replayed = await restarted.automation_service.get_automation(automation.id)
        assert third == first
        assert replayed.revision == revision

        with pytest.raises(ContractError) as conflict:
            await restarted.execute_command(
                context,
                "automation.resume",
                automation.id,
                {},
            )
        assert conflict.value.code is ErrorCode.CONFLICT

        audit = await restarted.automation_runtime_state.list_audit_events()
        actions = [
            event.get("action")
            for event in audit
            if event.get("type") == "automation.configuration"
        ]
        assert actions == ["created", "state_changed"]

    asyncio.run(scenario())


def test_global_event_and_scheduler_commands_require_internal_service_authority() -> None:
    async def scenario() -> None:
        control_plane, _kernel, _events = _stack()
        user = _context(key="user-evaluate")

        with pytest.raises(ContractError) as forbidden_evaluate:
            await control_plane.execute_command(
                user,
                "automation.evaluate",
                "automations",
                {},
            )
        assert forbidden_evaluate.value.code is ErrorCode.FORBIDDEN

        with pytest.raises(ContractError) as forbidden_event:
            await control_plane.execute_command(
                replace(user, idempotency_key="user-event"),
                "automation.event",
                "automations",
                {
                    "event_id": new_id("event"),
                    "event_type": "task.created",
                    "payload": {},
                },
            )
        assert forbidden_event.value.code is ErrorCode.FORBIDDEN

        service = _context(
            key="service-evaluate",
            principal_ref="service:automation-runtime",
            owner_type="service",
            owner_id="automation-runtime",
            actor_type="service",
        )
        with pytest.raises(ContractError) as spoofed_time:
            await control_plane.execute_command(
                service,
                "automation.evaluate",
                "automations",
                {"now": datetime.now(UTC).isoformat()},
            )
        assert spoofed_time.value.code is ErrorCode.INVALID_REQUEST

    asyncio.run(scenario())


def test_asgi_lifespan_starts_and_stops_automation_runtime() -> None:
    async def scenario() -> None:
        control_plane, _kernel, _events = _stack()
        app = ControlPlaneASGI(ControlPlaneHTTP(control_plane))
        received = iter(
            (
                {"type": "lifespan.startup"},
                {"type": "lifespan.shutdown"},
            )
        )
        sent: list[dict[str, Any]] = []
        saw_running = False

        async def receive() -> dict[str, Any]:
            nonlocal saw_running
            message = next(received)
            if message["type"] == "lifespan.shutdown":
                saw_running = control_plane.automation_runtime.running
            return message

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app({"type": "lifespan"}, receive, send)

        assert saw_running is True
        assert control_plane.automation_runtime.running is False
        assert [message["type"] for message in sent] == [
            "lifespan.startup.complete",
            "lifespan.shutdown.complete",
        ]

    asyncio.run(scenario())
