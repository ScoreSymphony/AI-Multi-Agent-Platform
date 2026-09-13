from __future__ import annotations

from typing import cast

from ai_multi_agent_platform.agents.execution_profile import (
    AGENT_EXECUTION_CAPABILITY_IDS_KEY,
    AgentExecutionBinding,
    decode_agent_execution_binding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Task, new_id


def test_agent_execution_binding_survives_canonical_task_metadata_freeze() -> None:
    binding = AgentExecutionBinding(
        agent_id=new_id("agent"),
        agent_revision=3,
        model_config_id="model-evaluation-target",
        capability_ids=("capability.shell", "capability.files"),
        workspace_id=new_id("workspace"),
    )
    task = Task(
        title="Frozen Agent execution binding",
        owner_ref=OwnerRef(type="service", id="evaluation-test"),
        metadata=encode_agent_execution_binding(binding),
    )

    assert task.metadata[AGENT_EXECUTION_CAPABILITY_IDS_KEY] == (
        "capability.shell",
        "capability.files",
    )
    decoded = decode_agent_execution_binding(
        cast(dict[str, JsonValue], task.metadata),
    )
    assert decoded == binding


def test_agent_execution_binding_rejects_non_array_capabilities_after_freeze_support() -> None:
    metadata = encode_agent_execution_binding(
        AgentExecutionBinding(agent_id=new_id("agent")),
    )
    metadata[AGENT_EXECUTION_CAPABILITY_IDS_KEY] = "capability.shell"

    try:
        decode_agent_execution_binding(metadata)
    except ValueError as exc:
        assert str(exc) == "agent.execution.capability_ids must be an array"
    else:
        raise AssertionError("non-array capability metadata must be rejected")
