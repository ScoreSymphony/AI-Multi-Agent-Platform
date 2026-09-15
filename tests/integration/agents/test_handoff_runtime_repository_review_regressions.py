from __future__ import annotations

import asyncio
from typing import cast

from ai_multi_agent_platform.agents import AgentRepository, AgentRevisionRef
from ai_multi_agent_platform.context import ContextSourceRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.handoffs import (
    AgentHandoff,
    DurableConsumedHandoffContextAdapter,
    HandoffConsumption,
    HandoffContent,
    InMemoryHandoffRepository,
    build_handoff,
)
from ai_multi_agent_platform.handoffs.async_repository import AsyncHandoffRepository
from ai_multi_agent_platform.handoffs.repository import HandoffRepository


class _ExplodingCompatibilityRepository(InMemoryHandoffRepository):
    def get_handoff(self, handoff_id: str, revision: int | None = None) -> AgentHandoff:
        raise AssertionError(
            f"synchronous compatibility repository read used for {handoff_id}@{revision}"
        )

    def list_consumptions_for_run(self, run_id: str) -> tuple[HandoffConsumption, ...]:
        raise AssertionError(f"synchronous compatibility repository read used for run {run_id}")


class _CustomAsyncRepository:
    def __init__(self, handoff: AgentHandoff, consumption: HandoffConsumption) -> None:
        self.handoff = handoff
        self.consumption = consumption
        self.calls: list[str] = []

    async def create_handoff(self, handoff, **kwargs):  # type: ignore[no-untyped-def]
        del handoff, kwargs
        raise AssertionError("unexpected create")

    async def get_handoff(self, handoff_id, revision=None):  # type: ignore[no-untyped-def]
        self.calls.append("get_handoff")
        assert handoff_id == self.handoff.handoff_id
        assert revision == self.handoff.revision
        return self.handoff

    async def list_handoffs(self):  # type: ignore[no-untyped-def]
        raise AssertionError("unexpected list")

    async def list_handoffs_for_task(self, task_id):  # type: ignore[no-untyped-def]
        del task_id
        raise AssertionError("unexpected task list")

    async def list_handoffs_for_step(self, step_id):  # type: ignore[no-untyped-def]
        del step_id
        raise AssertionError("unexpected step list")

    async def bind_consumption(self, consumption):  # type: ignore[no-untyped-def]
        del consumption
        raise AssertionError("unexpected bind")

    async def list_consumptions(self, handoff_id, revision):  # type: ignore[no-untyped-def]
        del handoff_id, revision
        raise AssertionError("unexpected consumption list")

    async def list_consumptions_for_run(self, run_id):  # type: ignore[no-untyped-def]
        self.calls.append("list_consumptions_for_run")
        assert run_id == self.consumption.consuming_run_id
        return (self.consumption,)


def test_durable_context_adapter_reuses_configured_async_repository() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    task_id = new_id("task")
    plan_id = new_id("plan")
    consumer_step_id = new_id("step")
    run_id = new_id("run")
    handoff = build_handoff(
        handoff_id=new_id("handoff"),
        revision=1,
        content=HandoffContent(
            task_id=task_id,
            plan_id=plan_id,
            producer_step_id=new_id("step"),
            consumer_step_id=consumer_step_id,
            producer_run_id=new_id("run"),
            producer=producer,
            intended_consumer=consumer,
            objective="Transfer reviewed work.",
            completed_work_summary="Producer work is complete.",
            recommended_next_action="Continue the consumer step.",
            requested_output="Verified consumer result.",
        ),
    )
    consumption = HandoffConsumption(
        handoff_id=handoff.handoff_id,
        handoff_revision=handoff.revision,
        handoff_digest=handoff.content_digest,
        consuming_run_id=run_id,
        consumer=consumer,
    )
    runtime_repository = _CustomAsyncRepository(handoff, consumption)
    adapter = DurableConsumedHandoffContextAdapter(
        repository=cast(HandoffRepository, _ExplodingCompatibilityRepository()),
        agents=cast(AgentRepository, object()),
        runtime_repository=cast(AsyncHandoffRepository, runtime_repository),
    )

    async def scenario() -> None:
        candidates = await adapter.collect(
            ContextSourceRequest(
                task_id=task_id,
                run_id=run_id,
                agent_id=consumer.agent_id,
                agent_revision=consumer.revision,
                project_id=None,
                workspace_id=None,
                plan_id=plan_id,
                step_id=consumer_step_id,
            )
        )
        assert len(candidates) == 1
        assert candidates[0].source.source_id == handoff.handoff_id
        assert runtime_repository.calls == ["list_consumptions_for_run", "get_handoff"]

    asyncio.run(scenario())
