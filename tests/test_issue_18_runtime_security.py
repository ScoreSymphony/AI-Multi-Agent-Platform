from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from ai_multi_agent_platform.automation import (
    AutomationRuntime,
    AutomationService,
    IdentityContext,
    InMemoryAutomationRuntimeState,
    ReferenceScheduler,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    RequestContext,
)
from ai_multi_agent_platform.domain import Event, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _identity(owner_id: str = "runtime-owner") -> IdentityContext:
    return IdentityContext(
        principal_ref=f"user:{owner_id}",
        owner_type="user",
        owner_id=owner_id,
    )


def _template() -> TaskTemplate:
    return TaskTemplate(
        title="Runtime scope task",
        objective="Exercise canonical event and authorization scope",
    )


def _context(key: str = "runtime-security") -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        actor=ActorContext(
            principal_ref="user:runtime-owner",
            owner_type="user",
            owner_id="runtime-owner",
        ),
        idempotency_key=key,
    )


def _stack(
    *,
    authorization: FakeAuthorizationProvider | None = None,
    state_path: Path | None = None,
) -> tuple[ControlPlane, PlatformKernel, InMemoryKernelRepository]:
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=events,
        authorization=authorization or FakeAuthorizationProvider(),
        automation_state_path=state_path,
    )
    return control_plane, kernel, events


class ProjectScopedAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_project_id: str) -> None:
        super().__init__()
        self.denied_project_id = denied_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        project_id = request.context.project_id
        return AuthorizationDecision(
            allowed=project_id != self.denied_project_id,
            reason="project-scope",
        )


class FailingCanonicalEventService:
    def __init__(self, bad_event_id: str, *, retryable: bool) -> None:
        self.bad_event_id = bad_event_id
        self.retryable = retryable
        self.processed: list[str] = []

    async def deliver_canonical_platform_event(self, event: Event) -> tuple[()]:
        if event.id == self.bad_event_id:
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE if self.retryable else ErrorCode.CONTRACT_VIOLATION,
                "synthetic canonical event failure",
                retryable=self.retryable,
            )
        self.processed.append(event.id)
        return ()


class TrackingScheduler:
    def __init__(self) -> None:
        self.tick_calls = 0

    async def tick(self, *, now: datetime | None = None) -> tuple[()]:
        del now
        self.tick_calls += 1
        return ()

    async def next_wakeup(self) -> datetime | None:
        return None


def test_canonical_event_runtime_ignores_historical_and_cross_owner_events() -> None:
    async def scenario() -> None:
        control_plane, kernel, _events = _stack()

        await kernel.create_task(
            idempotency_key="historical-task",
            title="Historical",
            objective="Must not backfire a later subscription",
            owner_type="user",
            owner_id="runtime-owner",
        )
        await asyncio.sleep(0.001)

        automation = await control_plane.automation_service.create_automation(
            name="owner event watcher",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="task.created",
            ),
            task_template=_template(),
        )

        await kernel.create_task(
            idempotency_key="foreign-task",
            title="Foreign",
            objective="Must not cross the canonical owner boundary",
            owner_type="user",
            owner_id="other-owner",
        )
        await kernel.create_task(
            idempotency_key="own-task",
            title="Own",
            objective="Should trigger exactly one Automation delivery",
            owner_type="user",
            owner_id="runtime-owner",
        )

        tick = await control_plane.automation_runtime.run_once()
        deliveries = await control_plane.automation_service.list_deliveries(automation.id)

        assert len(tick.processed_event_ids) == 3
        assert tick.failed_event_ids == ()
        assert tick.terminal_event_ids == ()
        assert len(tick.event_delivery_ids) == 1
        assert len(deliveries) == 1
        assert deliveries[0].generated_task_id is not None

    asyncio.run(scenario())


def test_automation_create_authorizes_requested_project_scope() -> None:
    async def scenario() -> None:
        denied_project_id = new_id("project")
        authorization = ProjectScopedAuthorization(denied_project_id)
        control_plane, _kernel, _events = _stack(authorization=authorization)

        with pytest.raises(ContractError) as denied:
            await control_plane.execute_command(
                _context("project-create"),
                "automation.create",
                "automations",
                {
                    "name": "forbidden project Automation",
                    "project_id": denied_project_id,
                    "trigger": {"type": "manual"},
                    "task_template": {
                        "title": "Task",
                        "objective": "Must not gain project scope",
                    },
                },
            )

        assert denied.value.code is ErrorCode.FORBIDDEN
        assert any(call.context.project_id == denied_project_id for call in authorization.calls)
        assert await control_plane.automation_service.list_automations() == ()

    asyncio.run(scenario())


def test_idempotent_replay_rechecks_current_authorization(tmp_path: Path) -> None:
    async def scenario() -> None:
        authorization = FakeAuthorizationProvider(allowed=True)
        control_plane, _kernel, _events = _stack(
            authorization=authorization,
            state_path=tmp_path / "replay-authorization.sqlite3",
        )
        automation = await control_plane.automation_service.create_automation(
            name="revocable replay",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
        )
        context = _context("revocable-pause")

        first = await control_plane.execute_command(
            context,
            "automation.pause",
            automation.id,
            {},
        )
        authorization.allowed = False

        with pytest.raises(ContractError) as denied:
            await control_plane.execute_command(
                context,
                "automation.pause",
                automation.id,
                {},
            )

        assert denied.value.code is ErrorCode.FORBIDDEN
        refreshed = await control_plane.automation_service.get_automation(automation.id)
        assert refreshed.revision == first["revision"]

    asyncio.run(scenario())


def test_terminal_event_failure_does_not_block_later_events_or_scheduler() -> None:
    async def scenario() -> None:
        events = InMemoryKernelRepository()
        state = InMemoryAutomationRuntimeState()
        aware_now = datetime.now(UTC)
        bad_stream = new_id("task")
        good_stream = new_id("task")
        bad = Event(
            event_type="runtime.test",
            subject_type="task",
            subject_id=bad_stream,
            correlation_id=bad_stream,
            occurred_at=datetime.now(),
        )
        good = Event(
            event_type="runtime.test",
            subject_type="task",
            subject_id=good_stream,
            correlation_id=good_stream,
            occurred_at=aware_now,
        )
        await events.commit(
            stream_id=bad_stream,
            expected_revision=0,
            events=(bad,),
        )
        await events.commit(
            stream_id=good_stream,
            expected_revision=0,
            events=(good,),
        )

        service = FailingCanonicalEventService(bad.id, retryable=False)
        scheduler = TrackingScheduler()
        runtime = AutomationRuntime(
            service=cast(AutomationService, service),
            scheduler=cast(ReferenceScheduler, scheduler),
            events=events,
            state=state,
        )

        tick = await runtime.run_once(now=aware_now)
        audit = await state.list_audit_events()

        assert tick.processed_event_ids == (good.id,)
        assert tick.failed_event_ids == ()
        assert tick.terminal_event_ids == (bad.id,)
        assert service.processed == [good.id]
        assert scheduler.tick_calls == 1
        assert await state.has_processed_event(good.id) is True
        assert await state.has_processed_event(bad.id) is True
        assert audit[-1]["type"] == "automation.runtime-event-terminal-failure"
        assert audit[-1]["event_id"] == bad.id
        assert audit[-1]["error_code"] == ErrorCode.CONTRACT_VIOLATION.value
        assert isinstance(runtime.last_error, ContractError)

    asyncio.run(scenario())


def test_retryable_event_failure_remains_unacknowledged() -> None:
    async def scenario() -> None:
        events = InMemoryKernelRepository()
        state = InMemoryAutomationRuntimeState()
        aware_now = datetime.now(UTC)
        bad_stream = new_id("task")
        bad = Event(
            event_type="runtime.retry",
            subject_type="task",
            subject_id=bad_stream,
            correlation_id=bad_stream,
            occurred_at=aware_now,
        )
        await events.commit(
            stream_id=bad_stream,
            expected_revision=0,
            events=(bad,),
        )

        service = FailingCanonicalEventService(bad.id, retryable=True)
        scheduler = TrackingScheduler()
        runtime = AutomationRuntime(
            service=cast(AutomationService, service),
            scheduler=cast(ReferenceScheduler, scheduler),
            events=events,
            state=state,
        )

        tick = await runtime.run_once(now=aware_now)

        assert tick.processed_event_ids == ()
        assert tick.failed_event_ids == (bad.id,)
        assert tick.terminal_event_ids == ()
        assert scheduler.tick_calls == 1
        assert await state.has_processed_event(bad.id) is False
        assert await state.list_audit_events() == ()
        assert isinstance(runtime.last_error, ContractError)

    asyncio.run(scenario())
