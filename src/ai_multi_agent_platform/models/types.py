"""Canonical model inventory and routing value types.

Provider-native model identifiers belong in namespaced adapter metadata. The
platform-facing model configuration ID remains stable across provider endpoint,
model-name and runtime-topology changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    HealthStatus,
    JsonValue,
    ModelRequest,
)


class ModelLocation(StrEnum):
    """Backend-neutral deployment classification for one model configuration."""

    LOCAL = "local"
    SELF_HOSTED = "self_hosted"
    REMOTE = "remote"


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Backend-neutral capabilities used for routing decisions."""

    context_window: int | None = None
    tool_calling: bool = False
    structured_output: bool = False
    streaming: bool = False
    modalities: tuple[str, ...] = ("text",)
    reasoning: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError("context_window must be greater than zero")
        if not self.modalities or any(not item.strip() for item in self.modalities):
            raise ValueError("modalities must contain non-blank values")
        if len(self.modalities) != len(set(self.modalities)):
            raise ValueError("modalities must be unique")
        if any(not item.strip() for item in self.reasoning):
            raise ValueError("reasoning metadata values must not be blank")
        if len(self.reasoning) != len(set(self.reasoning)):
            raise ValueError("reasoning metadata values must be unique")


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    """One canonical, registry-owned model configuration.

    ``config_id`` is the stable platform identity. Provider-native identifiers
    must be stored in ``adapter_metadata`` and therefore never become canonical
    references in Agent/Task definitions.
    """

    config_id: str
    display_name: str
    provider_id: str
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    revision: int = 1
    aliases: tuple[str, ...] = ()
    location: ModelLocation = ModelLocation.REMOTE
    node_ref: str | None = None
    health: HealthStatus = HealthStatus.UNKNOWN
    enabled: bool = True
    priority: int = 0
    resource_hints: dict[str, JsonValue] = field(default_factory=dict)
    cost_metadata: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.config_id.strip():
            raise ValueError("model config_id must not be blank")
        if not self.display_name.strip():
            raise ValueError("model display_name must not be blank")
        if not self.provider_id.strip():
            raise ValueError("model provider_id must not be blank")
        if self.revision <= 0:
            raise ValueError("model revision must be greater than zero")
        if self.node_ref is not None and not self.node_ref.strip():
            raise ValueError("node_ref must not be blank when provided")
        if any(not alias.strip() for alias in self.aliases):
            raise ValueError("model aliases must not be blank")
        if len(self.aliases) != len(set(self.aliases)):
            raise ValueError("model aliases must be unique")
        namespaces = [item.namespace for item in self.adapter_metadata]
        if len(namespaces) != len(set(namespaces)):
            raise ValueError("adapter metadata namespaces must be unique per model config")


@dataclass(frozen=True, slots=True)
class RoutingRequirements:
    """Typed canonical requirements consumed by the deterministic router."""

    explicit_model_id: str | None = None
    min_context_window: int | None = None
    tool_calling: bool = False
    structured_output: bool = False
    streaming: bool = False
    modalities: tuple[str, ...] = ()
    reasoning: tuple[str, ...] = ()
    local_only: bool = False
    self_hosted_only: bool = False

    def __post_init__(self) -> None:
        if self.explicit_model_id is not None and not self.explicit_model_id.strip():
            raise ValueError("explicit_model_id must not be blank")
        if self.min_context_window is not None and self.min_context_window <= 0:
            raise ValueError("min_context_window must be greater than zero")
        self._validate_unique_strings(self.modalities, "modalities")
        self._validate_unique_strings(self.reasoning, "reasoning")
        if self.local_only and self.self_hosted_only:
            raise ValueError("local_only and self_hosted_only are mutually exclusive policies")

    @staticmethod
    def _validate_unique_strings(values: tuple[str, ...], field_name: str) -> None:
        if any(not item.strip() for item in values):
            raise ValueError(f"required {field_name} must not be blank")
        if len(values) != len(set(values)):
            raise ValueError(f"required {field_name} must be unique")

    @classmethod
    def from_request(cls, request: ModelRequest) -> RoutingRequirements:
        """Parse the baseline ``ModelRequest.requirements`` into typed routing policy."""

        values = request.requirements

        def optional_string(key: str) -> str | None:
            value = values.get(key)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            return value

        def optional_positive_int(key: str) -> int | None:
            value = values.get(key)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
            return value

        def boolean(key: str) -> bool:
            value = values.get(key, False)
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
            return value

        def string_tuple(key: str) -> tuple[str, ...]:
            raw_values = values.get(key, [])
            if not isinstance(raw_values, list):
                raise ValueError(f"{key} must be a list of strings")
            parsed: list[str] = []
            for item in raw_values:
                if not isinstance(item, str):
                    raise ValueError(f"{key} must be a list of strings")
                parsed.append(item)
            return tuple(parsed)

        return cls(
            explicit_model_id=optional_string("model_config_id"),
            min_context_window=optional_positive_int("min_context_window"),
            tool_calling=boolean("tool_calling"),
            structured_output=boolean("structured_output"),
            streaming=boolean("streaming"),
            modalities=string_tuple("modalities"),
            reasoning=string_tuple("reasoning"),
            local_only=boolean("local_only"),
            self_hosted_only=boolean("self_hosted_only"),
        )


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """Explainable deterministic routing result."""

    model_config_id: str
    provider_id: str
    reason: str
    candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.model_config_id.strip():
            raise ValueError("model_config_id must not be blank")
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be blank")
        if not self.reason.strip():
            raise ValueError("routing reason must not be blank")
