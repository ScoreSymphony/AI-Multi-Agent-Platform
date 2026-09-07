"""Canonical context assembly models for issue #590.

The context layer owns composition evidence only. Source systems remain authoritative
for Task, Agent, Memory, Knowledge, Skill, Research, Repository and File lifecycles.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _optional_nonblank(value: str | None, name: str) -> None:
    if value is not None:
        _require_nonblank(value, name)


def _freeze_json_value(value: JsonValue) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _freeze_json_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    frozen = MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})
    return cast(Mapping[str, JsonValue], frozen)


def _thaw_json_value(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw_json_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported frozen JSON value: {type(value).__name__}")


def _thaw_json_mapping(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: _thaw_json_value(item) for key, item in value.items()}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class ContextSourceType(StrEnum):
    SYSTEM_SECURITY = "system_security"
    TASK = "task"
    PLAN_STEP = "plan_step"
    AGENT = "agent"
    SKILL = "skill"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    RESEARCH_EVIDENCE = "research_evidence"
    REPOSITORY = "repository"
    FILE = "file"
    ARTIFACT = "artifact"
    RESULT = "result"
    PRIOR_RUN = "prior_run"
    VERIFICATION = "verification"
    HUMAN = "human"


class ContextEntryRole(StrEnum):
    SECURITY = "security"
    INSTRUCTION = "instruction"
    CONTEXT = "context"
    EVIDENCE = "evidence"


class ContextFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class ContextTrust(StrEnum):
    SYSTEM = "system"
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    UNKNOWN = "unknown"


class ContextDataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    SECRET_REFERENCE = "secret_reference"


class ContextOmissionReason(StrEnum):
    UNAUTHORIZED = "unauthorized"
    APPROVAL_REQUIRED = "approval_required"
    PROJECT_SCOPE_MISMATCH = "project_scope_mismatch"
    WORKSPACE_SCOPE_MISMATCH = "workspace_scope_mismatch"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    CLASSIFICATION = "classification"
    DUPLICATE = "duplicate"
    BUDGET = "budget"
    CONFLICT = "conflict"


class ContextTransformationKind(StrEnum):
    TRUNCATE = "truncate"
    SUMMARIZE = "summarize"
    COMPACT = "compact"


class ContextConflictPolicy(StrEnum):
    PRESERVE = "preserve"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class ContextSourceRef:
    """Exact canonical source observation referenced by one context candidate/entry."""

    source_type: ContextSourceType
    source_id: str
    revision: str | None = None
    digest: str | None = None
    snapshot_id: str | None = None
    locator: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.source_id, "context source ID")
        for value, name in (
            (self.revision, "context source revision"),
            (self.digest, "context source digest"),
            (self.snapshot_id, "context source snapshot ID"),
            (self.locator, "context source locator"),
        ):
            _optional_nonblank(value, name)

    @property
    def canonical_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.source_type.value,
            self.source_id,
            self.revision or "",
            self.digest or "",
            self.snapshot_id or "",
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "revision": self.revision,
            "digest": self.digest,
            "snapshot_id": self.snapshot_id,
            "locator": self.locator,
        }


@dataclass(frozen=True, slots=True)
class ContextTransformation:
    kind: ContextTransformationKind
    policy_version: str
    input_digest: str
    output_digest: str
    details: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in (
            (self.policy_version, "context transformation policy version"),
            (self.input_digest, "context transformation input digest"),
            (self.output_digest, "context transformation output digest"),
        ):
            _require_nonblank(value, name)
        object.__setattr__(self, "details", _freeze_json_mapping(self.details))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "policy_version": self.policy_version,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "details": _thaw_json_mapping(self.details),
        }


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    """Source-owned context observation before policy/authorization/budget resolution."""

    source: ContextSourceRef
    role: ContextEntryRole
    selection_reason: str
    mandatory: bool = False
    inline_content: str | None = None
    content_ref: str | None = None
    content_digest: str | None = None
    freshness: ContextFreshness = ContextFreshness.CURRENT
    trust: ContextTrust = ContextTrust.UNKNOWN
    data_classification: ContextDataClassification = ContextDataClassification.INTERNAL
    priority: int = 0
    relevance: float = 0.0
    estimated_tokens: int | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    conflict_key: str | None = None
    security_labels: tuple[str, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.selection_reason, "context selection reason")
        if (self.inline_content is None) == (self.content_ref is None):
            raise ValueError("context candidate requires exactly one of inline_content or content_ref")
        if self.inline_content is not None and not self.inline_content:
            raise ValueError("inline context content must not be empty")
        _optional_nonblank(self.content_ref, "context content reference")
        _optional_nonblank(self.content_digest, "context content digest")
        _optional_nonblank(self.project_id, "context project ID")
        _optional_nonblank(self.workspace_id, "context workspace ID")
        _optional_nonblank(self.conflict_key, "context conflict key")
        if self.estimated_tokens is not None and self.estimated_tokens < 0:
            raise ValueError("estimated_tokens must be >= 0")
        if not 0.0 <= self.relevance <= 1.0:
            raise ValueError("context relevance must be between 0 and 1")
        if len(set(self.security_labels)) != len(self.security_labels):
            raise ValueError("context security labels must be unique")
        if any(not item.strip() for item in self.security_labels):
            raise ValueError("context security labels must not contain blanks")
        if (
            self.trust is ContextTrust.UNTRUSTED
            and self.role in {ContextEntryRole.SECURITY, ContextEntryRole.INSTRUCTION}
        ):
            raise ValueError("untrusted context cannot be promoted to security/instruction authority")
        if (
            self.data_classification is ContextDataClassification.SECRET_REFERENCE
            and self.inline_content is not None
        ):
            raise ValueError("secret-classified context must remain a reference, never inline content")
        if self.inline_content is not None:
            digest = _sha256_text(self.inline_content)
            if self.content_digest is not None and self.content_digest != digest:
                raise ValueError("inline context content_digest does not match content")
            object.__setattr__(self, "content_digest", digest)
        elif self.content_digest is None:
            raise ValueError("referenced context requires an exact content_digest")
        object.__setattr__(self, "security_labels", tuple(self.security_labels))
        object.__setattr__(self, "metadata", _freeze_json_mapping(self.metadata))

    @property
    def deduplication_key(self) -> tuple[str, str, str, str, str, str]:
        return (*self.source.canonical_key, cast(str, self.content_digest))


@dataclass(frozen=True, slots=True)
class ContextEntry:
    ordinal: int
    source: ContextSourceRef
    role: ContextEntryRole
    selection_reason: str
    mandatory: bool
    content_digest: str
    inline_content: str | None = None
    content_ref: str | None = None
    freshness: ContextFreshness = ContextFreshness.CURRENT
    trust: ContextTrust = ContextTrust.UNKNOWN
    data_classification: ContextDataClassification = ContextDataClassification.INTERNAL
    priority: int = 0
    relevance: float = 0.0
    estimated_tokens: int = 0
    content_bytes: int = 0
    project_id: str | None = None
    workspace_id: str | None = None
    conflict_key: str | None = None
    security_labels: tuple[str, ...] = ()
    transformation: ContextTransformation | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("context entry ordinal must be >= 0")
        _require_nonblank(self.selection_reason, "context selection reason")
        _require_nonblank(self.content_digest, "context content digest")
        if (self.inline_content is None) == (self.content_ref is None):
            raise ValueError("context entry requires exactly one of inline_content or content_ref")
        if self.inline_content is not None:
            if _sha256_text(self.inline_content) != self.content_digest:
                raise ValueError("context entry content digest does not match inline content")
            if len(self.inline_content.encode("utf-8")) != self.content_bytes:
                raise ValueError("context entry content_bytes does not match inline content")
        if self.estimated_tokens < 0 or self.content_bytes < 0:
            raise ValueError("context entry budget accounting cannot be negative")
        if not 0.0 <= self.relevance <= 1.0:
            raise ValueError("context relevance must be between 0 and 1")
        if (
            self.trust is ContextTrust.UNTRUSTED
            and self.role in {ContextEntryRole.SECURITY, ContextEntryRole.INSTRUCTION}
        ):
            raise ValueError("untrusted context cannot have instruction authority")
        if (
            self.data_classification is ContextDataClassification.SECRET_REFERENCE
            and self.inline_content is not None
        ):
            raise ValueError("secret-classified context must remain a reference")
        object.__setattr__(self, "security_labels", tuple(self.security_labels))
        object.__setattr__(self, "metadata", _freeze_json_mapping(self.metadata))

    def to_json(self, *, include_inline_content: bool = True) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "ordinal": self.ordinal,
            "source": self.source.to_json(),
            "role": self.role.value,
            "selection_reason": self.selection_reason,
            "mandatory": self.mandatory,
            "content_digest": self.content_digest,
            "content_ref": self.content_ref,
            "freshness": self.freshness.value,
            "trust": self.trust.value,
            "data_classification": self.data_classification.value,
            "priority": self.priority,
            "relevance": self.relevance,
            "estimated_tokens": self.estimated_tokens,
            "content_bytes": self.content_bytes,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "conflict_key": self.conflict_key,
            "security_labels": list(self.security_labels),
            "transformation": (
                None if self.transformation is None else self.transformation.to_json()
            ),
            "metadata": _thaw_json_mapping(self.metadata),
        }
        payload["inline_content"] = self.inline_content if include_inline_content else None
        return payload


@dataclass(frozen=True, slots=True)
class ContextOmission:
    source: ContextSourceRef
    reason: ContextOmissionReason
    mandatory: bool
    detail: str
    content_digest: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.detail, "context omission detail")
        _optional_nonblank(self.content_digest, "context omission content digest")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "source": self.source.to_json(),
            "reason": self.reason.value,
            "mandatory": self.mandatory,
            "detail": self.detail,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_tokens: int | None = None
    max_bytes: int | None = None
    max_items: int | None = None

    def __post_init__(self) -> None:
        if self.max_tokens is None and self.max_bytes is None and self.max_items is None:
            raise ValueError("context budget requires at least one limit")
        for value, name in (
            (self.max_tokens, "max_tokens"),
            (self.max_bytes, "max_bytes"),
            (self.max_items, "max_items"),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be >= 0")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "max_tokens": self.max_tokens,
            "max_bytes": self.max_bytes,
            "max_items": self.max_items,
        }


@dataclass(frozen=True, slots=True)
class ContextBudgetUsage:
    estimated_tokens: int = 0
    bytes: int = 0
    items: int = 0
    estimator_id: str = "utf8-bytes-div4-v1"

    def __post_init__(self) -> None:
        if self.estimated_tokens < 0 or self.bytes < 0 or self.items < 0:
            raise ValueError("context budget usage cannot be negative")
        _require_nonblank(self.estimator_id, "context token estimator ID")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "estimated_tokens": self.estimated_tokens,
            "bytes": self.bytes,
            "items": self.items,
            "estimator_id": self.estimator_id,
        }


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """Immutable effective context identity bound to a canonical Agent/Model Run."""

    context_bundle_id: str
    task_id: str
    run_id: str
    agent_id: str
    agent_revision: int
    entries: tuple[ContextEntry, ...]
    omissions: tuple[ContextOmission, ...]
    budget: ContextBudget
    usage: ContextBudgetUsage
    resolver_version: str
    policy_version: str
    actor_ref: str
    plan_id: str | None = None
    step_id: str | None = None
    skill_bundle_id: str | None = None
    skill_bundle_digest: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    reproducibility_limited: bool = False

    def __post_init__(self) -> None:
        validate_id(self.context_bundle_id, "context_bundle")
        validate_id(self.task_id, "task")
        validate_id(self.run_id, "run")
        validate_id(self.agent_id, "agent")
        if self.agent_revision < 1:
            raise ValueError("context bundle agent_revision must be >= 1")
        for value, name in (
            (self.resolver_version, "context resolver version"),
            (self.policy_version, "context policy version"),
            (self.actor_ref, "context actor reference"),
        ):
            _require_nonblank(value, name)
        _optional_nonblank(self.plan_id, "context plan ID")
        _optional_nonblank(self.step_id, "context step ID")
        _optional_nonblank(self.skill_bundle_id, "skill bundle ID")
        _optional_nonblank(self.skill_bundle_digest, "skill bundle digest")
        if (self.skill_bundle_id is None) != (self.skill_bundle_digest is None):
            raise ValueError("skill bundle ID and digest must be supplied together")
        expected_ordinals = tuple(range(len(self.entries)))
        if tuple(entry.ordinal for entry in self.entries) != expected_ordinals:
            raise ValueError("context bundle entries must have contiguous canonical ordinals")
        if self.usage.items != len(self.entries):
            raise ValueError("context bundle item usage must equal entry count")
        if self.usage.bytes != sum(item.content_bytes for item in self.entries):
            raise ValueError("context bundle byte usage does not match entries")
        if self.usage.estimated_tokens != sum(item.estimated_tokens for item in self.entries):
            raise ValueError("context bundle token usage does not match entries")

    def canonical_payload(self) -> dict[str, JsonValue]:
        return {
            "schema": "context-bundle/v1",
            "task_id": self.task_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "agent_revision": self.agent_revision,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "skill_bundle_id": self.skill_bundle_id,
            "skill_bundle_digest": self.skill_bundle_digest,
            "entries": [item.to_json(include_inline_content=True) for item in self.entries],
            "omissions": [item.to_json() for item in self.omissions],
            "budget": self.budget.to_json(),
            "usage": self.usage.to_json(),
            "resolver_version": self.resolver_version,
            "policy_version": self.policy_version,
            "actor_ref": self.actor_ref,
            "reproducibility_limited": self.reproducibility_limited,
        }

    def compute_digest(self) -> str:
        return hashlib.sha256(_canonical_json(cast(JsonValue, self.canonical_payload()))).hexdigest()

    @property
    def digest(self) -> str:
        return self.compute_digest()

    def to_json(self, *, include_inline_content: bool = True) -> dict[str, JsonValue]:
        return {
            "schema": "context-bundle/v1",
            "context_bundle_id": self.context_bundle_id,
            "digest": self.digest,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "agent_revision": self.agent_revision,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "skill_bundle_id": self.skill_bundle_id,
            "skill_bundle_digest": self.skill_bundle_digest,
            "entries": [
                item.to_json(include_inline_content=include_inline_content)
                for item in self.entries
            ],
            "omissions": [item.to_json() for item in self.omissions],
            "budget": self.budget.to_json(),
            "usage": self.usage.to_json(),
            "resolver_version": self.resolver_version,
            "policy_version": self.policy_version,
            "actor_ref": self.actor_ref,
            "created_at": self.created_at.isoformat(),
            "reproducibility_limited": self.reproducibility_limited,
        }


def new_context_bundle_id() -> str:
    return new_id("context_bundle")
