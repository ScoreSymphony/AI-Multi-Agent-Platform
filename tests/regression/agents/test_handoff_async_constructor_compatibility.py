from __future__ import annotations

from typing import cast

from ai_multi_agent_platform.agents import AgentRepository
from ai_multi_agent_platform.handoffs import (
    DurableConsumedHandoffContextAdapter,
    HandoffRepository,
)


def test_durable_handoff_adapter_preserves_third_positional_adapter_id() -> None:
    repository = cast(HandoffRepository, object())
    agents = cast(AgentRepository, object())

    adapter = DurableConsumedHandoffContextAdapter(repository, agents, "custom-adapter")

    assert adapter.adapter_id == "custom-adapter"
    assert adapter.runtime_repository is None
