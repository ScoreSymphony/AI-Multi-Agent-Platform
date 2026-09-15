from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.handoffs import (
    HandoffContent,
    HandoffService,
    InMemoryHandoffAuditSink,
    InMemoryHandoffRepository,
)


class _Agents:
    def get_agent_revision(self, agent_id: str, revision: int) -> object:
        del agent_id, revision
        return object()

    def get_team_revision(self, team_id: str, revision: int) -> object:
        del team_id, revision
        return object()


class _References:
    def exists(self, reference: object) -> bool:
        del reference
        return True

    def can_read(self, participant: object, reference: object) -> bool:
        del participant, reference
        return True


class _BlockingAsyncRepository:
    def __init__(self, repository: InMemoryHandoffRepository) -> None:
        self.repository = repository
        self.create_started = asyncio.Event()
        self.release_create = asyncio.Event()
        self.bind_started = asyncio.Event()
        self.release_bind = asyncio.Event()
        self.block_create = False
        self.block_bind = False

    async def create_handoff(self, handoff, **kwargs):  # type: ignore[no-untyped-def]
        if self.block_create:
            self.create_started.set()
            await self.release_create.wait()
        return self.repository.create_handoff(handoff, **kwargs)

    async def get_handoff(self, handoff_id, revision=None):  # type: ignore[no-untyped-def]
        return self.repository.get_handoff(handoff_id, revision)

    async def list_handoffs(self):  # type: ignore[no-untyped-def]
        return self.repository.list_handoffs()

    async def list_handoffs_for_task(self, task_id):  # type: ignore[no-untyped-def]
        return self.repository.list_handoffs_for_task(task_id)

    async def list_handoffs_for_step(self, step_id):  # type: ignore[no-untyped-def]
        return self.repository.list_handoffs_for_step(step_id)

    async def bind_consumption(self, consumption):  # type: ignore[no-untyped-def]
        if self.block_bind:
            self.bind_started.set()
            await self.release_bind.wait()
        return self.repository.bind_consumption(consumption)

    async def list_consumptions(self, handoff_id, revision):  # type: ignore[no-untyped-def]
        return self.repository.list_consumptions(handoff_id, revision)

    async def list_consumptions_for_run(self, run_id):  # type: ignore[no-untyped-def]
        return self.repository.list_consumptions_for_run(run_id)


class _FailingAudit:
    def record(self, event: object) -> None:
        del event
        raise RuntimeError("audit unavailable")


def _content(producer: AgentRevisionRef, consumer: AgentRevisionRef) -> HandoffContent:
    return HandoffContent(
        task_id=new_id("task"),
        plan_id=new_id("plan"),
        producer_step_id=new_id("step"),
        consumer_step_id=new_id("step"),
        producer_run_id=new_id("run"),
        producer=producer,
        intended_consumer=consumer,
        objective="Transfer reviewed work.",
        completed_work_summary="Producer work is complete.",
        recommended_next_action="Continue the consumer step.",
        requested_output="Verified consumer result.",
    )


def _service(
    repository: InMemoryHandoffRepository,
    runtime_repository: _BlockingAsyncRepository,
    audit: object,
) -> HandoffService:
    return HandoffService(
        repository,
        agents=_Agents(),  # type: ignore[arg-type]
        references=_References(),  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        runtime_repository=runtime_repository,
    )


def test_creation_cancellation_waits_through_required_audit() -> None:
    async def scenario() -> None:
        repository = InMemoryHandoffRepository()
        runtime_repository = _BlockingAsyncRepository(repository)
        runtime_repository.block_create = True
        audit = InMemoryHandoffAuditSink()
        service = _service(repository, runtime_repository, audit)
        producer = AgentRevisionRef(new_id("agent"), 1)
        consumer = AgentRevisionRef(new_id("agent"), 1)
        handoff_id = new_id("handoff")

        write = asyncio.create_task(
            service.async_create_handoff(
                _content(producer, consumer),
                idempotency_key="review-cancellation-create",
                handoff_id=handoff_id,
            )
        )
        await runtime_repository.create_started.wait()
        write.cancel()
        await asyncio.sleep(0)
        assert not write.done()

        runtime_repository.release_create.set()
        with pytest.raises(asyncio.CancelledError):
            await write

        stored = repository.get_handoff(handoff_id, 1)
        assert stored.handoff_id == handoff_id
        assert [event.event_type for event in audit.events] == ["handoff.created"]

    asyncio.run(scenario())


def test_consumption_cancellation_waits_through_required_audit() -> None:
    async def scenario() -> None:
        repository = InMemoryHandoffRepository()
        runtime_repository = _BlockingAsyncRepository(repository)
        audit = InMemoryHandoffAuditSink()
        service = _service(repository, runtime_repository, audit)
        producer = AgentRevisionRef(new_id("agent"), 1)
        consumer = AgentRevisionRef(new_id("agent"), 1)
        handoff = service.create_handoff(
            _content(producer, consumer),
            idempotency_key="review-consumption-seed",
        )
        audit.events.clear()
        runtime_repository.block_bind = True
        run_id = new_id("run")

        write = asyncio.create_task(
            service.async_consume_handoff(
                handoff.handoff_id,
                handoff.revision,
                consuming_run_id=run_id,
                consumer=consumer,
            )
        )
        await runtime_repository.bind_started.wait()
        write.cancel()
        await asyncio.sleep(0)
        assert not write.done()

        runtime_repository.release_bind.set()
        with pytest.raises(asyncio.CancelledError):
            await write

        consumptions = repository.list_consumptions(handoff.handoff_id, handoff.revision)
        assert len(consumptions) == 1
        assert consumptions[0].consuming_run_id == run_id
        assert [event.event_type for event in audit.events] == ["handoff.consumed"]

    asyncio.run(scenario())


def test_audit_failure_wins_over_pending_creation_cancellation() -> None:
    async def scenario() -> None:
        repository = InMemoryHandoffRepository()
        runtime_repository = _BlockingAsyncRepository(repository)
        runtime_repository.block_create = True
        service = _service(repository, runtime_repository, _FailingAudit())
        producer = AgentRevisionRef(new_id("agent"), 1)
        consumer = AgentRevisionRef(new_id("agent"), 1)
        handoff_id = new_id("handoff")

        write = asyncio.create_task(
            service.async_create_handoff(
                _content(producer, consumer),
                idempotency_key="review-audit-failure",
                handoff_id=handoff_id,
            )
        )
        await runtime_repository.create_started.wait()
        write.cancel()
        runtime_repository.release_create.set()

        with pytest.raises(RuntimeError, match="audit unavailable"):
            await write
        assert repository.get_handoff(handoff_id, 1).handoff_id == handoff_id

    asyncio.run(scenario())
