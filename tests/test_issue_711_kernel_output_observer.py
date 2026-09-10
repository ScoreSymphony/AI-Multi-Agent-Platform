from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.testing import FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend


class RecordingObserver:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.events: list[PlatformEvent] = []

    async def output_attached(self, event: PlatformEvent) -> None:
        self.events.append(event)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated observer interruption after canonical persistence")


async def _task(kernel: PlatformKernel) -> str:
    task = await kernel.create_task(
        idempotency_key="issue-711-observer:create",
        title="Output observer",
        objective="Prove post-commit output observation and retry recovery.",
        owner_type="user",
        owner_id="issue-711",
    )
    return task.task_id


def test_output_observer_runs_after_persisted_result_and_replays_on_idempotent_retry() -> None:
    async def scenario() -> None:
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
        )
        observer = RecordingObserver()
        kernel.configure_output_attachment_observer(observer)
        task_id = await _task(kernel)
        result_id = new_id("result")

        first = await kernel.attach_result(
            idempotency_key="issue-711-observer:result",
            task_id=task_id,
            result_id=result_id,
        )
        assert result_id in first.result_ids
        assert len(observer.events) == 1
        assert observer.events[0].event_type == "result.attached"
        assert observer.events[0].payload["result_id"] == result_id

        repeated = await kernel.attach_result(
            idempotency_key="issue-711-observer:result",
            task_id=task_id,
            result_id=result_id,
        )
        assert result_id in repeated.result_ids
        assert len(observer.events) == 2
        attachment_events = [
            event
            for event in await kernel.history(task_id)
            if event.event_type == "result.attached"
        ]
        assert len(attachment_events) == 1
        assert observer.events[0].id == observer.events[1].id == attachment_events[0].id

    asyncio.run(scenario())


def test_observer_failure_keeps_attachment_durable_and_retry_resumes_observation() -> None:
    async def scenario() -> None:
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
        )
        observer = RecordingObserver(fail_once=True)
        kernel.configure_output_attachment_observer(observer)
        task_id = await _task(kernel)
        artifact_id = new_id("artifact")

        with pytest.raises(RuntimeError, match="observer interruption"):
            await kernel.attach_artifact(
                idempotency_key="issue-711-observer:artifact",
                task_id=task_id,
                artifact_id=artifact_id,
            )

        persisted = await kernel.get_task(task_id)
        assert artifact_id in persisted.artifact_ids
        attachment_events = [
            event
            for event in await kernel.history(task_id)
            if event.event_type == "artifact.attached"
        ]
        assert len(attachment_events) == 1

        recovered = await kernel.attach_artifact(
            idempotency_key="issue-711-observer:artifact",
            task_id=task_id,
            artifact_id=artifact_id,
        )
        assert artifact_id in recovered.artifact_ids
        assert len(observer.events) == 2
        assert observer.events[0].id == observer.events[1].id == attachment_events[0].id
        assert len(
            [
                event
                for event in await kernel.history(task_id)
                if event.event_type == "artifact.attached"
            ]
        ) == 1

    asyncio.run(scenario())


def test_attachment_retry_with_different_output_fails_closed_before_observer() -> None:
    async def scenario() -> None:
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
        )
        observer = RecordingObserver()
        kernel.configure_output_attachment_observer(observer)
        task_id = await _task(kernel)
        first_result = new_id("result")
        second_result = new_id("result")

        await kernel.attach_result(
            idempotency_key="issue-711-observer:same-command",
            task_id=task_id,
            result_id=first_result,
        )
        with pytest.raises(ContractError) as exc_info:
            await kernel.attach_result(
                idempotency_key="issue-711-observer:same-command",
                task_id=task_id,
                result_id=second_result,
            )
        assert exc_info.value.code is ErrorCode.CONFLICT
        assert len(observer.events) == 1
        persisted = await kernel.get_task(task_id)
        assert first_result in persisted.result_ids
        assert second_result not in persisted.result_ids

    asyncio.run(scenario())
