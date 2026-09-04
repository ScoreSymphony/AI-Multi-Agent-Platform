"""Provider-neutral conversational response contract for issue #72."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

from .models import ConversationMessage, ModelRoutingPreference


class ConversationResponseChunkKind(StrEnum):
    TEXT = "text"
    ACTIVITY = "activity"


@dataclass(frozen=True, slots=True)
class ConversationResponseTarget:
    """Canonical target identity exposed to a replaceable conversational responder."""

    kind: str
    id: str
    revision: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"agent", "agent_team", "project", "task", "orchestrator"}:
            raise ValueError("unsupported conversation response target kind")
        if not self.id.strip():
            raise ValueError("conversation response target id must not be blank")
        if self.revision is not None and self.revision < 1:
            raise ValueError("conversation response target revision must be positive")
        if self.kind == "orchestrator" and self.id != "platform":
            raise ValueError("canonical orchestrator response target must be platform")

    def to_json(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {"kind": self.kind, "id": self.id}
        if self.revision is not None:
            result["revision"] = self.revision
        return result


@dataclass(frozen=True, slots=True)
class ConversationResponseRequest:
    """Canonical input for one explicit conversational response operation."""

    request_id: str
    correlation_id: str
    actor_ref: str
    conversation_id: str
    source_message_id: str
    target: ConversationResponseTarget
    history: tuple[ConversationMessage, ...]
    project_id: str | None = None
    model_preference: ModelRoutingPreference | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_id, "request_id"),
            (self.correlation_id, "correlation_id"),
            (self.actor_ref, "actor_ref"),
            (self.conversation_id, "conversation_id"),
            (self.source_message_id, "source_message_id"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if not self.history:
            raise ValueError("conversation response requires durable message history")
        if self.history[-1].id != self.source_message_id:
            raise ValueError("conversation response source message must be the latest history item")


@dataclass(frozen=True, slots=True)
class ConversationResponseChunk:
    """One tentative provider-neutral response item.

    Chunks deliberately cannot carry tool commands, credentials, provider-native session
    identifiers, responder-private identities or arbitrary metadata. Privileged work must
    still enter canonical Task and approval paths instead of being interpreted from model
    output.
    """

    kind: ConversationResponseChunkKind
    text: str
    model_config_id: str | None = None

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("conversation response chunks require non-empty text")
        if self.model_config_id is not None and not self.model_config_id.strip():
            raise ValueError("model_config_id must not be blank")


class ConversationResponseProvider(Protocol):
    """Replaceable orchestrator/model boundary for conversational response text."""

    def stream_response(
        self,
        request: ConversationResponseRequest,
    ) -> AsyncIterator[ConversationResponseChunk]: ...


__all__ = [
    "ConversationResponseChunk",
    "ConversationResponseChunkKind",
    "ConversationResponseProvider",
    "ConversationResponseRequest",
    "ConversationResponseTarget",
]
