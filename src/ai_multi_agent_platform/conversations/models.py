"""Canonical conversation and message contracts for issue #72.

Conversation state is an interaction shell over canonical platform resources. It must
never become an alternate Task/Run lifecycle or persist backend-private model sessions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id


class ConversationStatus(StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"
    TOMBSTONED = "tombstoned"


class MessageStatus(StrEnum):
    ACTIVE = "active"
    EDITED = "edited"
    TOMBSTONED = "tombstoned"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    EVENT = "event"


class ContentKind(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    JSON = "json"
    REFERENCE = "reference"


class ParticipantKind(StrEnum):
    USER = "user"
    SERVICE = "service"
    AGENT = "agent"
    AGENT_TEAM = "agent_team"


class ReferenceKind(StrEnum):
    FILE = "file"
    ARTIFACT = "artifact"
    TASK = "task"
    RUN = "run"
    RESULT = "result"
    AGENT = "agent"
    AGENT_TEAM = "agent_team"
    KNOWLEDGE = "knowledge"


_REFERENCE_PREFIX = {
    ReferenceKind.FILE: "file",
    ReferenceKind.ARTIFACT: "artifact",
    ReferenceKind.TASK: "task",
    ReferenceKind.RUN: "run",
    ReferenceKind.RESULT: "result",
    ReferenceKind.AGENT: "agent",
    ReferenceKind.AGENT_TEAM: "team",
    ReferenceKind.KNOWLEDGE: "knowledge_source",
}


@dataclass(frozen=True, slots=True)
class ConversationParticipant:
    kind: ParticipantKind
    id: str
    display_name: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ParticipantKind.AGENT:
            validate_id(self.id, "agent")
        elif self.kind is ParticipantKind.AGENT_TEAM:
            validate_id(self.id, "team")
        elif not self.id.strip():
            raise ValueError("participant id must not be blank")
        if self.display_name is not None and not self.display_name.strip():
            raise ValueError("participant display_name must not be blank")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "id": self.id,
            "display_name": self.display_name,
        }

    @classmethod
    def from_json(cls, value: object) -> ConversationParticipant:
        raw = _mapping(value, "participant")
        return cls(
            kind=ParticipantKind(_required_string(raw, "kind")),
            id=_required_string(raw, "id"),
            display_name=_optional_string(raw, "display_name"),
        )


@dataclass(frozen=True, slots=True)
class AgentSelectionRef:
    kind: ParticipantKind
    id: str
    revision: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in {ParticipantKind.AGENT, ParticipantKind.AGENT_TEAM}:
            raise ValueError("agent selection kind must be agent or agent_team")
        validate_id(self.id, "agent" if self.kind is ParticipantKind.AGENT else "team")
        if self.revision is not None and self.revision < 1:
            raise ValueError("agent selection revision must be >= 1")

    def to_json(self) -> dict[str, JsonValue]:
        return {"kind": self.kind.value, "id": self.id, "revision": self.revision}

    @classmethod
    def from_json(cls, value: object) -> AgentSelectionRef:
        raw = _mapping(value, "agent_selection")
        revision = raw.get("revision")
        if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool)):
            raise ValueError("agent_selection.revision must be an integer or null")
        return cls(
            kind=ParticipantKind(_required_string(raw, "kind")),
            id=_required_string(raw, "id"),
            revision=revision,
        )


@dataclass(frozen=True, slots=True)
class ModelRoutingPreference:
    """User-visible canonical routing preference, never a provider-native model id."""

    model_config_id: str | None = None
    routing_requirements: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.model_config_id is not None and not self.model_config_id.strip():
            raise ValueError("model_config_id must not be blank")
        object.__setattr__(
            self,
            "routing_requirements",
            MappingProxyType(dict(self.routing_requirements)),
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "model_config_id": self.model_config_id,
            "routing_requirements": dict(self.routing_requirements),
        }

    @classmethod
    def from_json(cls, value: object) -> ModelRoutingPreference:
        raw = _mapping(value, "model_preference")
        requirements = raw.get("routing_requirements", {})
        if not isinstance(requirements, Mapping):
            raise ValueError("routing_requirements must be an object")
        return cls(
            model_config_id=_optional_string(raw, "model_config_id"),
            routing_requirements=cast(Mapping[str, JsonValue], requirements),
        )


@dataclass(frozen=True, slots=True)
class ResourceReference:
    kind: ReferenceKind
    id: str
    label: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, _REFERENCE_PREFIX[self.kind])
        if self.label is not None and not self.label.strip():
            raise ValueError("reference label must not be blank")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "id": self.id,
            "label": self.label,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_json(cls, value: object) -> ResourceReference:
        raw = _mapping(value, "reference")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("reference.metadata must be an object")
        return cls(
            kind=ReferenceKind(_required_string(raw, "kind")),
            id=_required_string(raw, "id"),
            label=_optional_string(raw, "label"),
            metadata=cast(Mapping[str, JsonValue], metadata),
        )


@dataclass(frozen=True, slots=True)
class ConversationContentBlock:
    kind: ContentKind
    text: str | None = None
    value: JsonValue = None
    reference: ResourceReference | None = None

    def __post_init__(self) -> None:
        populated = (
            int(self.text is not None)
            + int(self.value is not None)
            + int(self.reference is not None)
        )
        if populated != 1:
            raise ValueError("content block must contain exactly one payload")
        if self.kind in {ContentKind.TEXT, ContentKind.MARKDOWN}:
            if self.text is None or not self.text:
                raise ValueError("text/markdown content requires non-empty text")
        elif self.kind is ContentKind.JSON:
            if self.value is None:
                raise ValueError("json content requires a value")
        elif self.reference is None:
            raise ValueError("reference content requires a canonical resource reference")

    @classmethod
    def text_block(cls, text: str) -> ConversationContentBlock:
        return cls(kind=ContentKind.TEXT, text=text)

    def to_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"kind": self.kind.value}
        if self.text is not None:
            payload["text"] = self.text
        if self.value is not None:
            payload["value"] = self.value
        if self.reference is not None:
            payload["reference"] = self.reference.to_json()
        return payload

    @classmethod
    def from_json(cls, value: object) -> ConversationContentBlock:
        raw = _mapping(value, "content block")
        reference_raw = raw.get("reference")
        return cls(
            kind=ContentKind(_required_string(raw, "kind")),
            text=_optional_string(raw, "text"),
            value=cast(JsonValue, raw.get("value")),
            reference=(
                ResourceReference.from_json(reference_raw) if reference_raw is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class Conversation:
    title: str
    owner_ref: str
    id: str = field(default_factory=lambda: new_id("conversation"))
    summary: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    participants: tuple[ConversationParticipant, ...] = ()
    status: ConversationStatus = ConversationStatus.OPEN
    default_agent: AgentSelectionRef | None = None
    model_preference: ModelRoutingPreference | None = None
    task_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    result_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, "conversation")
        if not self.title.strip():
            raise ValueError("conversation title must not be blank")
        if not self.owner_ref.strip():
            raise ValueError("conversation owner_ref must not be blank")
        if self.summary is not None and not self.summary.strip():
            raise ValueError("conversation summary must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")
        for task_id in self.task_ids:
            validate_id(task_id, "task")
        for run_id in self.run_ids:
            validate_id(run_id, "run")
        for artifact_id in self.artifact_ids:
            validate_id(artifact_id, "artifact")
        for result_id in self.result_ids:
            validate_id(result_id, "result")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        object.__setattr__(self, "participants", tuple(self.participants))
        object.__setattr__(self, "task_ids", _deduplicate(self.task_ids))
        object.__setattr__(self, "run_ids", _deduplicate(self.run_ids))
        object.__setattr__(self, "artifact_ids", _deduplicate(self.artifact_ids))
        object.__setattr__(self, "result_ids", _deduplicate(self.result_ids))
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        object.__setattr__(self, "updated_at", self.updated_at.astimezone(UTC))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "owner_ref": self.owner_ref,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "participants": [participant.to_json() for participant in self.participants],
            "status": self.status.value,
            "default_agent": self.default_agent.to_json() if self.default_agent else None,
            "model_preference": (
                self.model_preference.to_json() if self.model_preference is not None else None
            ),
            "task_ids": list(self.task_ids),
            "run_ids": list(self.run_ids),
            "artifact_ids": list(self.artifact_ids),
            "result_ids": list(self.result_ids),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_json(cls, value: object) -> Conversation:
        raw = _mapping(value, "conversation")
        participants = _sequence(raw.get("participants", ()), "participants")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("conversation.metadata must be an object")
        default_agent_raw = raw.get("default_agent")
        model_preference_raw = raw.get("model_preference")
        return cls(
            id=_required_string(raw, "id"),
            title=_required_string(raw, "title"),
            summary=_optional_string(raw, "summary"),
            owner_ref=_required_string(raw, "owner_ref"),
            project_id=_optional_string(raw, "project_id"),
            workspace_id=_optional_string(raw, "workspace_id"),
            participants=tuple(ConversationParticipant.from_json(item) for item in participants),
            status=ConversationStatus(_required_string(raw, "status")),
            default_agent=(
                AgentSelectionRef.from_json(default_agent_raw)
                if default_agent_raw is not None
                else None
            ),
            model_preference=(
                ModelRoutingPreference.from_json(model_preference_raw)
                if model_preference_raw is not None
                else None
            ),
            task_ids=_string_tuple(raw.get("task_ids", ()), "task_ids"),
            run_ids=_string_tuple(raw.get("run_ids", ()), "run_ids"),
            artifact_ids=_string_tuple(raw.get("artifact_ids", ()), "artifact_ids"),
            result_ids=_string_tuple(raw.get("result_ids", ()), "result_ids"),
            created_at=_datetime(raw, "created_at"),
            updated_at=_datetime(raw, "updated_at"),
            metadata=cast(Mapping[str, JsonValue], metadata),
        )


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    conversation_id: str
    sender_ref: str
    role: MessageRole
    content: tuple[ConversationContentBlock, ...]
    id: str = field(default_factory=lambda: new_id("message"))
    references: tuple[ResourceReference, ...] = ()
    model_config_id: str | None = None
    model_provider_ref: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    edited_at: datetime | None = None
    status: MessageStatus = MessageStatus.ACTIVE
    revision: int = 1
    correlation_id: str | None = None
    causation_id: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, "message")
        validate_id(self.conversation_id, "conversation")
        if not self.sender_ref.strip():
            raise ValueError("message sender_ref must not be blank")
        if not self.content:
            raise ValueError("message content must not be empty")
        if self.model_config_id is not None and not self.model_config_id.strip():
            raise ValueError("model_config_id must not be blank")
        if self.model_provider_ref is not None and not self.model_provider_ref.strip():
            raise ValueError("model_provider_ref must not be blank")
        _require_aware(self.created_at, "created_at")
        if self.edited_at is not None:
            _require_aware(self.edited_at, "edited_at")
            if self.edited_at < self.created_at:
                raise ValueError("edited_at must not be earlier than created_at")
        if self.revision < 1:
            raise ValueError("message revision must be >= 1")
        for field_name, value in (
            ("correlation_id", self.correlation_id),
            ("causation_id", self.causation_id),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        object.__setattr__(self, "content", tuple(self.content))
        object.__setattr__(self, "references", tuple(self.references))
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        if self.edited_at is not None:
            object.__setattr__(self, "edited_at", self.edited_at.astimezone(UTC))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sender_ref": self.sender_ref,
            "role": self.role.value,
            "content": [block.to_json() for block in self.content],
            "references": [reference.to_json() for reference in self.references],
            "model_config_id": self.model_config_id,
            "model_provider_ref": self.model_provider_ref,
            "created_at": self.created_at.isoformat(),
            "edited_at": self.edited_at.isoformat() if self.edited_at is not None else None,
            "status": self.status.value,
            "revision": self.revision,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_json(cls, value: object) -> ConversationMessage:
        raw = _mapping(value, "message")
        content = _sequence(raw.get("content"), "content")
        references = _sequence(raw.get("references", ()), "references")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("message.metadata must be an object")
        revision = raw.get("revision", 1)
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ValueError("message.revision must be an integer")
        edited_at_raw = raw.get("edited_at")
        edited_at = None if edited_at_raw is None else _datetime(raw, "edited_at")
        return cls(
            id=_required_string(raw, "id"),
            conversation_id=_required_string(raw, "conversation_id"),
            sender_ref=_required_string(raw, "sender_ref"),
            role=MessageRole(_required_string(raw, "role")),
            content=tuple(ConversationContentBlock.from_json(item) for item in content),
            references=tuple(ResourceReference.from_json(item) for item in references),
            model_config_id=_optional_string(raw, "model_config_id"),
            model_provider_ref=_optional_string(raw, "model_provider_ref"),
            created_at=_datetime(raw, "created_at"),
            edited_at=edited_at,
            status=MessageStatus(_required_string(raw, "status")),
            revision=revision,
            correlation_id=_optional_string(raw, "correlation_id"),
            causation_id=_optional_string(raw, "causation_id"),
            metadata=cast(Mapping[str, JsonValue], metadata),
        )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    return cast(Sequence[object], value)


def _required_string(mapping: Mapping[str, object], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _optional_string(mapping: Mapping[str, object], name: str) -> str | None:
    value = mapping.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string or null")
    return value


def _datetime(mapping: Mapping[str, object], name: str) -> datetime:
    value = _required_string(mapping, name)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    _require_aware(parsed, name)
    return parsed.astimezone(UTC)


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    sequence = _sequence(value, name)
    if not all(isinstance(item, str) and item.strip() for item in sequence):
        raise ValueError(f"{name} must contain only non-blank strings")
    return tuple(cast(Sequence[str], sequence))


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")


def _deduplicate(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
