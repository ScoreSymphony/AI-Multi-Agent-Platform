"""Canonical planning metadata for platform Tasks (issue #88)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

TASK_MANAGEMENT_METADATA_KEY = "task_management"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def rank(self) -> int:
        return {
            TaskPriority.LOW: 10,
            TaskPriority.NORMAL: 20,
            TaskPriority.HIGH: 30,
            TaskPriority.URGENT: 40,
        }[self]


class TaskDependencyKind(StrEnum):
    DEPENDS_ON = "depends_on"
    RELATED_TO = "related_to"


ResponsibilityKind = Literal["user", "team", "organization"]
AgentAssignmentKind = Literal["agent", "agent_team"]


@dataclass(frozen=True, slots=True)
class ResponsibilityRef:
    """Planning responsibility only; this reference never grants authorization."""

    kind: ResponsibilityKind
    id: str

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("responsibility id must not be blank")

    def to_json(self) -> dict[str, JsonValue]:
        return {"kind": self.kind, "id": self.id}

    @classmethod
    def from_json(cls, value: object) -> ResponsibilityRef:
        mapping = _mapping(value, "responsibility")
        kind = _required_string(mapping, "kind")
        if kind not in {"user", "team", "organization"}:
            raise ValueError("responsibility.kind must be user, team or organization")
        return cls(kind=cast(ResponsibilityKind, kind), id=_required_string(mapping, "id"))


@dataclass(frozen=True, slots=True)
class AgentAssignmentRef:
    """Reference to a canonical Agent/AgentTeam definition, not a provider process."""

    kind: AgentAssignmentKind
    id: str
    revision: int | None = None
    required: bool = False
    policy_ref: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.id, "agent" if self.kind == "agent" else "team")
        if self.revision is not None and self.revision < 1:
            raise ValueError("agent assignment revision must be >= 1")
        if self.policy_ref is not None and not self.policy_ref.strip():
            raise ValueError("agent assignment policy_ref must not be blank")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind,
            "id": self.id,
            "revision": self.revision,
            "required": self.required,
            "policy_ref": self.policy_ref,
        }

    @classmethod
    def from_json(cls, value: object) -> AgentAssignmentRef:
        mapping = _mapping(value, "agent_assignment")
        kind = _required_string(mapping, "kind")
        if kind not in {"agent", "agent_team"}:
            raise ValueError("agent_assignment.kind must be agent or agent_team")
        revision = mapping.get("revision")
        if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool)):
            raise ValueError("agent_assignment.revision must be an integer or null")
        required = mapping.get("required", False)
        if not isinstance(required, bool):
            raise ValueError("agent_assignment.required must be boolean")
        policy_ref = _optional_string(mapping, "policy_ref")
        return cls(
            kind=cast(AgentAssignmentKind, kind),
            id=_required_string(mapping, "id"),
            revision=revision,
            required=required,
            policy_ref=policy_ref,
        )


@dataclass(frozen=True, slots=True)
class TaskDependency:
    task_id: str
    kind: TaskDependencyKind = TaskDependencyKind.DEPENDS_ON

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")

    def to_json(self) -> dict[str, JsonValue]:
        return {"task_id": self.task_id, "kind": self.kind.value}

    @classmethod
    def from_json(cls, value: object) -> TaskDependency:
        mapping = _mapping(value, "dependency")
        raw_kind = _optional_string(mapping, "kind") or TaskDependencyKind.DEPENDS_ON.value
        try:
            kind = TaskDependencyKind(raw_kind)
        except ValueError as exc:
            raise ValueError(f"unsupported dependency kind: {raw_kind}") from exc
        return cls(task_id=_required_string(mapping, "task_id"), kind=kind)


@dataclass(frozen=True, slots=True)
class TaskPlanningMetadata:
    priority: TaskPriority = TaskPriority.NORMAL
    due_at: datetime | None = None
    deadline_timezone: str | None = None
    not_before: datetime | None = None
    responsibility: ResponsibilityRef | None = None
    agent_assignment: AgentAssignmentRef | None = None
    labels: tuple[str, ...] = ()
    workspace_id: str | None = None
    parent_task_id: str | None = None
    dependencies: tuple[TaskDependency, ...] = ()
    blocking_reason: str | None = None
    effort_hint: float | None = None
    resource_hints: Mapping[str, JsonValue] = field(default_factory=dict)
    archived: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        if self.due_at is not None:
            _require_aware(self.due_at, "due_at")
            object.__setattr__(self, "due_at", self.due_at.astimezone(UTC))
        if self.not_before is not None:
            _require_aware(self.not_before, "not_before")
            object.__setattr__(self, "not_before", self.not_before.astimezone(UTC))
        if self.deadline_timezone is not None and not self.deadline_timezone.strip():
            raise ValueError("deadline_timezone must not be blank")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")
        if self.parent_task_id is not None:
            validate_id(self.parent_task_id, "task")
        if self.blocking_reason is not None and not self.blocking_reason.strip():
            raise ValueError("blocking_reason must not be blank")
        if self.effort_hint is not None and self.effort_hint <= 0:
            raise ValueError("effort_hint must be greater than zero")

        normalized_labels: list[str] = []
        for label in self.labels:
            cleaned = label.strip()
            if not cleaned:
                raise ValueError("labels must not contain blank values")
            if cleaned not in normalized_labels:
                normalized_labels.append(cleaned)
        object.__setattr__(self, "labels", tuple(normalized_labels))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        object.__setattr__(self, "resource_hints", MappingProxyType(dict(self.resource_hints)))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "priority": self.priority.value,
            "due_at": self.due_at.isoformat() if self.due_at is not None else None,
            "deadline_timezone": self.deadline_timezone,
            "not_before": self.not_before.isoformat() if self.not_before is not None else None,
            "responsibility": self.responsibility.to_json() if self.responsibility else None,
            "agent_assignment": self.agent_assignment.to_json() if self.agent_assignment else None,
            "labels": list(self.labels),
            "workspace_id": self.workspace_id,
            "parent_task_id": self.parent_task_id,
            "dependencies": [dependency.to_json() for dependency in self.dependencies],
            "blocking_reason": self.blocking_reason,
            "effort_hint": self.effort_hint,
            "resource_hints": dict(self.resource_hints),
            "archived": self.archived,
            "hidden": self.hidden,
        }

    @classmethod
    def from_json(cls, value: object | None) -> TaskPlanningMetadata:
        if value is None:
            return cls()
        mapping = _mapping(value, TASK_MANAGEMENT_METADATA_KEY)
        priority_raw = _optional_string(mapping, "priority") or TaskPriority.NORMAL.value
        try:
            priority = TaskPriority(priority_raw)
        except ValueError as exc:
            raise ValueError(f"unsupported task priority: {priority_raw}") from exc
        labels_raw = mapping.get("labels", ())
        if not isinstance(labels_raw, (list, tuple)) or not all(
            isinstance(label, str) for label in labels_raw
        ):
            raise ValueError("labels must be a sequence of strings")
        dependencies_raw = mapping.get("dependencies", ())
        if not isinstance(dependencies_raw, (list, tuple)):
            raise ValueError("dependencies must be a sequence")
        resource_hints = mapping.get("resource_hints", {})
        if not isinstance(resource_hints, Mapping):
            raise ValueError("resource_hints must be an object")
        archived = mapping.get("archived", False)
        hidden = mapping.get("hidden", False)
        if not isinstance(archived, bool) or not isinstance(hidden, bool):
            raise ValueError("archived and hidden must be boolean")
        effort_hint_raw = mapping.get("effort_hint")
        effort_hint: float | None
        if effort_hint_raw is None:
            effort_hint = None
        elif isinstance(effort_hint_raw, (int, float)) and not isinstance(effort_hint_raw, bool):
            effort_hint = float(effort_hint_raw)
        else:
            raise ValueError("effort_hint must be a number or null")
        responsibility_raw = mapping.get("responsibility")
        assignment_raw = mapping.get("agent_assignment")
        return cls(
            priority=priority,
            due_at=_optional_datetime(mapping, "due_at"),
            deadline_timezone=_optional_string(mapping, "deadline_timezone"),
            not_before=_optional_datetime(mapping, "not_before"),
            responsibility=(
                ResponsibilityRef.from_json(responsibility_raw)
                if responsibility_raw is not None
                else None
            ),
            agent_assignment=(
                AgentAssignmentRef.from_json(assignment_raw) if assignment_raw is not None else None
            ),
            labels=tuple(cast(list[str] | tuple[str, ...], labels_raw)),
            workspace_id=_optional_string(mapping, "workspace_id"),
            parent_task_id=_optional_string(mapping, "parent_task_id"),
            dependencies=tuple(TaskDependency.from_json(item) for item in dependencies_raw),
            blocking_reason=_optional_string(mapping, "blocking_reason"),
            effort_hint=effort_hint,
            resource_hints=cast(Mapping[str, JsonValue], resource_hints),
            archived=archived,
            hidden=hidden,
        )

    def patch(self, changes: Mapping[str, JsonValue]) -> TaskPlanningMetadata:
        allowed = set(self.to_json())
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported task-management fields: {sorted(unknown)!r}")
        merged = self.to_json()
        merged.update(changes)
        return TaskPlanningMetadata.from_json(merged)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


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


def _optional_datetime(mapping: Mapping[str, object], name: str) -> datetime | None:
    value = mapping.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO 8601 string or null")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO 8601 datetime") from exc
    _require_aware(parsed, name)
    return parsed


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
