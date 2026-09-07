"""Canonical metadata contracts for routing Runs through AgentRuntime.

The keys in this module are platform-owned execution metadata, not onboarding-,
Evaluation- or Planning-private lifecycle state. Producers may bind either one exact
Agent execution request to a canonical Task or exact Agent execution requests to
canonical Step IDs; the lifecycle backend consumes the same contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

AGENT_EXECUTION_PROFILE_KEY = "agent.execution.profile"
AGENT_EXECUTION_PROFILE = "agent"
AGENT_EXECUTION_AGENT_ID_KEY = "agent.execution.agent_id"
AGENT_EXECUTION_AGENT_REVISION_KEY = "agent.execution.agent_revision"
AGENT_EXECUTION_MODEL_CONFIG_ID_KEY = "agent.execution.model_config_id"
AGENT_EXECUTION_CAPABILITY_IDS_KEY = "agent.execution.capability_ids"
AGENT_EXECUTION_WORKSPACE_ID_KEY = "agent.execution.workspace_id"
AGENT_STEP_EXECUTION_BINDINGS_KEY = "agent.execution.step_bindings"


@dataclass(frozen=True, slots=True)
class AgentExecutionBinding:
    """Exact canonical Agent execution identity decoded from platform metadata."""

    agent_id: str
    agent_revision: int | None = None
    model_config_id: str | None = None
    capability_ids: tuple[str, ...] = ()
    workspace_id: str | None = None


def decode_agent_execution_binding(
    metadata: Mapping[str, JsonValue],
) -> AgentExecutionBinding | None:
    """Decode the generic Agent execution profile, returning ``None`` when absent.

    Canonical domain metadata recursively freezes JSON arrays into tuples. The decoder
    therefore accepts both the ingress JSON representation (``list``) and the immutable
    canonical representation (``tuple``) while keeping element validation strict.
    """

    if metadata.get(AGENT_EXECUTION_PROFILE_KEY) != AGENT_EXECUTION_PROFILE:
        return None
    agent_id = _required_string(metadata, AGENT_EXECUTION_AGENT_ID_KEY)
    revision = _optional_positive_int(metadata, AGENT_EXECUTION_AGENT_REVISION_KEY)
    model_config_id = _optional_string(metadata, AGENT_EXECUTION_MODEL_CONFIG_ID_KEY)
    workspace_id = _optional_string(metadata, AGENT_EXECUTION_WORKSPACE_ID_KEY)
    raw_capabilities: object = metadata.get(AGENT_EXECUTION_CAPABILITY_IDS_KEY, [])
    if not isinstance(raw_capabilities, list | tuple):
        raise ValueError(f"{AGENT_EXECUTION_CAPABILITY_IDS_KEY} must be an array")
    capability_ids: list[str] = []
    for value in raw_capabilities:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{AGENT_EXECUTION_CAPABILITY_IDS_KEY} must contain non-blank strings")
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


def decode_agent_step_execution_binding(
    metadata: Mapping[str, JsonValue],
    step_id: str,
) -> AgentExecutionBinding | None:
    """Decode an exact Agent binding for one canonical Step ID when present."""

    validate_id(step_id, "step")
    raw_bindings = metadata.get(AGENT_STEP_EXECUTION_BINDINGS_KEY)
    if raw_bindings is None:
        return None
    if not isinstance(raw_bindings, Mapping):
        raise ValueError(f"{AGENT_STEP_EXECUTION_BINDINGS_KEY} must be an object")
    raw_binding = raw_bindings.get(step_id)
    if raw_binding is None:
        return None
    if not isinstance(raw_binding, Mapping):
        raise ValueError(
            f"{AGENT_STEP_EXECUTION_BINDINGS_KEY}[{step_id!r}] must be an object"
        )
    return decode_agent_execution_binding(raw_binding)


def encode_agent_execution_binding(binding: AgentExecutionBinding) -> dict[str, JsonValue]:
    """Encode one exact Agent execution binding into canonical metadata."""

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


def encode_agent_step_execution_bindings(
    bindings: Mapping[str, AgentExecutionBinding],
) -> dict[str, JsonValue]:
    """Encode exact Step-ID-scoped Agent bindings for canonical Task metadata."""

    encoded: dict[str, JsonValue] = {}
    for step_id, binding in bindings.items():
        validate_id(step_id, "step")
        encoded[step_id] = encode_agent_execution_binding(binding)
    return {AGENT_STEP_EXECUTION_BINDINGS_KEY: encoded}


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
    "AGENT_STEP_EXECUTION_BINDINGS_KEY",
    "AgentExecutionBinding",
    "decode_agent_execution_binding",
    "decode_agent_step_execution_binding",
    "encode_agent_execution_binding",
    "encode_agent_step_execution_bindings",
]
