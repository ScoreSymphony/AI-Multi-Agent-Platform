"""Canonical Task metadata contract for routing a Run through AgentRuntime.

The keys in this module are platform-owned execution metadata, not onboarding- or
Evaluation-private lifecycle state. Producers such as onboarding and Evaluation may
bind an exact Agent execution request to a canonical Task; the lifecycle backend
consumes the same contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ai_multi_agent_platform.contracts.types import JsonValue

AGENT_EXECUTION_PROFILE_KEY = "agent.execution.profile"
AGENT_EXECUTION_PROFILE = "agent"
AGENT_EXECUTION_AGENT_ID_KEY = "agent.execution.agent_id"
AGENT_EXECUTION_AGENT_REVISION_KEY = "agent.execution.agent_revision"
AGENT_EXECUTION_MODEL_CONFIG_ID_KEY = "agent.execution.model_config_id"
AGENT_EXECUTION_CAPABILITY_IDS_KEY = "agent.execution.capability_ids"
AGENT_EXECUTION_WORKSPACE_ID_KEY = "agent.execution.workspace_id"


@dataclass(frozen=True, slots=True)
class AgentExecutionBinding:
    """Exact canonical Agent execution identity decoded from Task metadata."""

    agent_id: str
    agent_revision: int | None = None
    model_config_id: str | None = None
    capability_ids: tuple[str, ...] = ()
    workspace_id: str | None = None


def decode_agent_execution_binding(
    metadata: Mapping[str, JsonValue],
) -> AgentExecutionBinding | None:
    """Decode the generic Agent execution profile, returning ``None`` when absent."""

    if metadata.get(AGENT_EXECUTION_PROFILE_KEY) != AGENT_EXECUTION_PROFILE:
        return None
    agent_id = _required_string(metadata, AGENT_EXECUTION_AGENT_ID_KEY)
    revision = _optional_positive_int(metadata, AGENT_EXECUTION_AGENT_REVISION_KEY)
    model_config_id = _optional_string(metadata, AGENT_EXECUTION_MODEL_CONFIG_ID_KEY)
    workspace_id = _optional_string(metadata, AGENT_EXECUTION_WORKSPACE_ID_KEY)
    raw_capabilities = metadata.get(AGENT_EXECUTION_CAPABILITY_IDS_KEY, [])
    if not isinstance(raw_capabilities, list):
        raise ValueError(f"{AGENT_EXECUTION_CAPABILITY_IDS_KEY} must be an array")
    capability_ids: list[str] = []
    for value in raw_capabilities:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"{AGENT_EXECUTION_CAPABILITY_IDS_KEY} must contain non-blank strings"
            )
        capability_ids.append(value)
    if len(capability_ids) != len(set(capability_ids)):
        raise ValueError(f"{AGENT_EXECUTION_CAPABILITY_IDS_KEY} must be unique")
    return AgentExecutionBinding(
        agent_id=agent_id,
        agent_revision=revision,
        model_config_id=model_config_id,
        capability_ids=tuple(capability_ids),
        workspace_id=workspace_id,
    )


def encode_agent_execution_binding(binding: AgentExecutionBinding) -> dict[str, JsonValue]:
    """Encode one exact Agent execution binding into canonical Task metadata."""

    payload: dict[str, JsonValue] = {
        AGENT_EXECUTION_PROFILE_KEY: AGENT_EXECUTION_PROFILE,
        AGENT_EXECUTION_AGENT_ID_KEY: binding.agent_id,
        AGENT_EXECUTION_CAPABILITY_IDS_KEY: list(binding.capability_ids),
    }
    if binding.agent_revision is not None:
        payload[AGENT_EXECUTION_AGENT_REVISION_KEY] = binding.agent_revision
    if binding.model_config_id is not None:
        payload[AGENT_EXECUTION_MODEL_CONFIG_ID_KEY] = binding.model_config_id
    if binding.workspace_id is not None:
        payload[AGENT_EXECUTION_WORKSPACE_ID_KEY] = binding.workspace_id
    return payload


def _required_string(metadata: Mapping[str, JsonValue], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _optional_string(metadata: Mapping[str, JsonValue], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string or null")
    return value


def _optional_positive_int(metadata: Mapping[str, JsonValue], key: str) -> int | None:
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer or null")
    return value


__all__ = [
    "AGENT_EXECUTION_AGENT_ID_KEY",
    "AGENT_EXECUTION_AGENT_REVISION_KEY",
    "AGENT_EXECUTION_CAPABILITY_IDS_KEY",
    "AGENT_EXECUTION_MODEL_CONFIG_ID_KEY",
    "AGENT_EXECUTION_PROFILE",
    "AGENT_EXECUTION_PROFILE_KEY",
    "AGENT_EXECUTION_WORKSPACE_ID_KEY",
    "AgentExecutionBinding",
    "decode_agent_execution_binding",
    "encode_agent_execution_binding",
]
