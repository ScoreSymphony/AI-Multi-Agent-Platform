from __future__ import annotations

import asyncio
import weakref
from typing import cast

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.handoffs import (
    AsyncHandoffRepositoryAdapter,
    HandoffContent,
    InMemoryHandoffRepository,
    build_handoff,
    compute_creation_request_digest,
)
from ai_multi_agent_platform.handoffs.repository import HandoffRepository


class _OpaqueRepository:
    """Protocol-compatible repository that is unhashable and cannot be weak-referenced."""

    __slots__ = ("_inner",)
    __hash__ = None

    def __init__(self) -> None:
        self._inner = InMemoryHandoffRepository()

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


def _handoff():
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    content = HandoffContent(
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
    return build_handoff(
        handoff_id=new_id("handoff"),
        revision=1,
        content=content,
    )


def test_unhashable_nonweakrefable_repository_can_share_handoff_offload() -> None:
    repository = _OpaqueRepository()
    with pytest.raises(TypeError):
        weakref.ref(repository)

    canonical = cast(HandoffRepository, repository)
    first = AsyncHandoffRepositoryAdapter(canonical)
    second = AsyncHandoffRepositoryAdapter(canonical)

    assert first.offload is second.offload

    handoff = _handoff()

    async def scenario() -> None:
        stored, created = await first.create_handoff(
            handoff,
            idempotency_key="opaque-handoff",
            request_digest=compute_creation_request_digest(handoff.content),
            expected_previous_revision=0,
        )
        assert created is True
        assert stored == handoff
        assert await second.get_handoff(handoff.handoff_id, 1) == handoff

    asyncio.run(scenario())
