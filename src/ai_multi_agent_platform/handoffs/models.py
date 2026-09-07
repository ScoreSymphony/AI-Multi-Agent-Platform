"""Canonical Agent Handoff value objects for structured multi-agent work transfer.

Handoffs intentionally do not own Task/Plan/Step/Run progression. They record the exact
semantic work-transfer boundary between canonical Agent/AgentTeam revisions and reference
source resources without copying those source payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Literal, cast

from ai_multi_agent_platform.agents.models import AgentRevisionRef, AgentTeamRevisionRef
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Provenance, new_id, validate_id

HANDOFF_SCHEMA_VERSION = "1.0"

ParticipantRef = AgentRevisionRef | AgentTeamRevisionRef


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _require_digest(value: str, name: str) -> str:
    candidate = value.lower()
    if len(candidate) != 64 or any(char not in "0123456789abcdef" for char in candidate):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return candidate


def _freeze_strings(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    frozen = tuple(values)
    for value in frozen:
        _require_nonblank(value, name)
    if len(set(frozen)) != len(frozen):
        raise ValueError(f"{name} values must be unique")
    return frozen


def _participant_payload(ref: ParticipantRef) -> dict[str, JsonValue]:
    if isinstance(ref, AgentRevisionRef):
        return {"kind": "agent", "id": ref.agent_id, "revision": ref.revision}
    return {"kind": "team", "id": ref.team_id, "revision": ref.revision}


def participant_key(ref: ParticipantRef) -> tuple[str, str, int]:
    payload = _participant_payload(ref)
    return cast(str, payload["kind"]), cast(str, payload["id"]), cast(int, payload["revision"])


def _participant_from_payload(payload: Mapping[str, Any]) -> ParticipantRef:
    kind = str(payload["kind"])
    resource_id = str(payload["id"])
    revision = int(payload["revision"])
    if kind == "agent":
        return AgentRevisionRef(agent_id=resource_id, revision=revision)
    if kind == "team":
        return AgentTeamRevisionRef(team_id=resource_id, revision=revision)
    raise ValueError(f"unsupported handoff participant kind: {kind}")


class HandoffSourceKind(StrEnum):
    ARTIFACT = "artifact"
    RESULT = "result"
    RESEARCH_CLAIM = "research_claim"
    RESEARCH_EVIDENCE = "research_evidence"
    CONTEXT_BUNDLE = "context_bundle"
    SKILL_BUNDLE = "skill_bundle"


@dataclass(frozen=True, slots=True)
class HandoffSourceRef:
    """Exact reference to authoritative source material used by a Handoff.

    Large or mutable payloads remain in their owning subsystem. ``revision`` and ``digest``
    pin the strongest exact identity currently available without inventing a shadow resource.
    """

    kind: HandoffSourceKind
    resource_id: str
    revision: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        if self.kind is HandoffSourceKind.ARTIFACT:
            validate_id(self.resource_id, "artifact")
        elif self.kind is HandoffSourceKind.RESULT:
            validate_id(self.resource_id, "result")
        else:
            _require_nonblank(self.resource_id, "handoff source resource_id")
        if self.revision is not None:
            _require_nonblank(self.revision, "handoff source revision")
        if self.digest is not None:
            object.__setattr__(self, "digest", _require_digest(self.digest, "handoff source digest"))

    @property
    def identity(self) -> tuple[str, str, str | None, str | None]:
        return self.kind.value, self.resource_id, self.revision, self.digest


@dataclass(frozen=True, slots=True)
class HandoffContent:
    """Immutable semantic content intentionally transferred to the next work boundary."""

    task_id: str
    plan_id: str
    producer_step_id: str
    consumer_step_id: str
    producer_run_id: str
    producer: ParticipantRef
    objective: str
    completed_work_summary: str
    recommended_next_action: str
    requested_output: str
    intended_consumer: ParticipantRef | None = None
    consumer_requirements: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    source_refs: tuple[HandoffSourceRef, ...] = ()
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        validate_id(self.plan_id, "plan")
        validate_id(self.producer_step_id, "step")
        validate_id(self.consumer_step_id, "step")
        validate_id(self.producer_run_id, "run")
        _require_nonblank(self.objective, "handoff objective")
        _require_nonblank(self.completed_work_summary, "completed work summary")
        _require_nonblank(self.recommended_next_action, "recommended next action")
        _require_nonblank(self.requested_output, "requested output")
        object.__setattr__(
            self,
            "consumer_requirements",
            _freeze_strings(self.consumer_requirements, "consumer requirement"),
        )
        object.__setattr__(
            self,
            "unresolved_questions",
            _freeze_strings(self.unresolved_questions, "unresolved question"),
        )
        object.__setattr__(self, "blockers", _freeze_strings(self.blockers, "blocker"))
        object.__setattr__(
            self,
            "assumptions",
            _freeze_strings(self.assumptions, "handoff assumption"),
        )
        object.__setattr__(
            self,
            "constraints",
            _freeze_strings(self.constraints, "handoff constraint"),
        )
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        identities = [ref.identity for ref in self.source_refs]
        if len(set(identities)) != len(identities):
            raise ValueError("handoff source references must be unique")
        if self.intended_consumer is None and not self.consumer_requirements:
            raise ValueError("handoff requires an intended consumer or consumer requirements")


@dataclass(frozen=True, slots=True)
class AgentHandoff:
    """Canonical immutable/versioned handoff evidence."""

    handoff_id: str
    revision: int
    content: HandoffContent
    content_digest: str
    created_at: datetime = field(default_factory=_utc_now)
    schema_version: str = HANDOFF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.handoff_id, "handoff")
        if self.revision < 1:
            raise ValueError("handoff revision must be >= 1")
        if self.schema_version != HANDOFF_SCHEMA_VERSION:
            raise ValueError(f"unsupported handoff schema version: {self.schema_version}")
        object.__setattr__(
            self,
            "content_digest",
            _require_digest(self.content_digest, "handoff content_digest"),
        )
        expected = compute_handoff_digest(
            handoff_id=self.handoff_id,
            revision=self.revision,
            content=self.content,
            schema_version=self.schema_version,
        )
        if self.content_digest != expected:
            raise ValueError("handoff content_digest does not match canonical content")

    @property
    def task_id(self) -> str:
        return self.content.task_id

    @property
    def producer(self) -> ParticipantRef:
        return self.content.producer

    @property
    def intended_consumer(self) -> ParticipantRef | None:
        return self.content.intended_consumer


@dataclass(frozen=True, slots=True)
class HandoffConsumption:
    """Durable binding proving which exact Handoff a consuming Run used."""

    handoff_id: str
    handoff_revision: int
    handoff_digest: str
    consuming_run_id: str
    consumer: ParticipantRef
    context_bundle_ref: HandoffSourceRef | None = None
    consumed_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        validate_id(self.handoff_id, "handoff")
        validate_id(self.consuming_run_id, "run")
        if self.handoff_revision < 1:
            raise ValueError("handoff consumption revision must be >= 1")
        object.__setattr__(
            self,
            "handoff_digest",
            _require_digest(self.handoff_digest, "handoff consumption digest"),
        )
        if (
            self.context_bundle_ref is not None
            and self.context_bundle_ref.kind is not HandoffSourceKind.CONTEXT_BUNDLE
        ):
            raise ValueError("context_bundle_ref must use context_bundle source kind")


@dataclass(frozen=True, slots=True)
class HandoffContextSource:
    """Provider-neutral source candidate that #590 can include in a ContextBundle."""

    handoff_id: str
    revision: int
    digest: str
    task_id: str
    plan_id: str
    producer_step_id: str
    consumer_step_id: str
    selection_reason: Literal["intentional_agent_handoff"] = "intentional_agent_handoff"
    source_type: Literal["agent_handoff"] = "agent_handoff"

    def __post_init__(self) -> None:
        validate_id(self.handoff_id, "handoff")
        validate_id(self.task_id, "task")
        validate_id(self.plan_id, "plan")
        validate_id(self.producer_step_id, "step")
        validate_id(self.consumer_step_id, "step")
        if self.revision < 1:
            raise ValueError("handoff context-source revision must be >= 1")
        object.__setattr__(self, "digest", _require_digest(self.digest, "handoff context digest"))


@dataclass(frozen=True, slots=True)
class HandoffRuntimeContext:
    """Consumption result returned only after the exact Run binding is durable."""

    handoff: AgentHandoff
    consumption: HandoffConsumption
    context_source: HandoffContextSource


@dataclass(frozen=True, slots=True)
class HandoffAuditEvent:
    event_type: str
    handoff_id: str
    revision: int
    task_id: str
    consuming_run_id: str | None = None
    details: Mapping[str, JsonValue] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _require_nonblank(self.event_type, "handoff audit event type")
        validate_id(self.handoff_id, "handoff")
        validate_id(self.task_id, "task")
        if self.revision < 1:
            raise ValueError("handoff audit revision must be >= 1")
        if self.consuming_run_id is not None:
            validate_id(self.consuming_run_id, "run")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


def new_handoff_id() -> str:
    return new_id("handoff")


def _source_ref_payload(ref: HandoffSourceRef) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "kind": ref.kind.value,
        "resource_id": ref.resource_id,
    }
    if ref.revision is not None:
        payload["revision"] = ref.revision
    if ref.digest is not None:
        payload["digest"] = ref.digest
    return payload


def _provenance_payload(provenance: Provenance | None) -> dict[str, Any] | None:
    if provenance is None:
        return None
    return {
        "source": provenance.source,
        "actor_ref": provenance.actor_ref,
        "details": _canonical_value(dict(provenance.details)),
    }


def handoff_content_to_dict(content: HandoffContent) -> dict[str, Any]:
    return {
        "task_id": content.task_id,
        "plan_id": content.plan_id,
        "producer_step_id": content.producer_step_id,
        "consumer_step_id": content.consumer_step_id,
        "producer_run_id": content.producer_run_id,
        "producer": _participant_payload(content.producer),
        "intended_consumer": (
            _participant_payload(content.intended_consumer)
            if content.intended_consumer is not None
            else None
        ),
        "consumer_requirements": list(content.consumer_requirements),
        "objective": content.objective,
        "completed_work_summary": content.completed_work_summary,
        "unresolved_questions": list(content.unresolved_questions),
        "blockers": list(content.blockers),
        "assumptions": list(content.assumptions),
        "constraints": list(content.constraints),
        "source_refs": [_source_ref_payload(ref) for ref in content.source_refs],
        "recommended_next_action": content.recommended_next_action,
        "requested_output": content.requested_output,
        "provenance": _provenance_payload(content.provenance),
    }


def compute_creation_request_digest(content: HandoffContent) -> str:
    payload = json.dumps(
        _canonical_value(handoff_content_to_dict(content)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def compute_handoff_digest(
    *,
    handoff_id: str,
    revision: int,
    content: HandoffContent,
    schema_version: str = HANDOFF_SCHEMA_VERSION,
) -> str:
    payload = {
        "schema_version": schema_version,
        "handoff_id": handoff_id,
        "revision": revision,
        "content": handoff_content_to_dict(content),
    }
    encoded = json.dumps(
        _canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_handoff(
    *,
    handoff_id: str,
    revision: int,
    content: HandoffContent,
    created_at: datetime | None = None,
) -> AgentHandoff:
    return AgentHandoff(
        handoff_id=handoff_id,
        revision=revision,
        content=content,
        created_at=created_at or _utc_now(),
        content_digest=compute_handoff_digest(
            handoff_id=handoff_id,
            revision=revision,
            content=content,
        ),
    )


def handoff_context_source(handoff: AgentHandoff) -> HandoffContextSource:
    content = handoff.content
    return HandoffContextSource(
        handoff_id=handoff.handoff_id,
        revision=handoff.revision,
        digest=handoff.content_digest,
        task_id=content.task_id,
        plan_id=content.plan_id,
        producer_step_id=content.producer_step_id,
        consumer_step_id=content.consumer_step_id,
    )


def handoff_to_dict(handoff: AgentHandoff) -> dict[str, Any]:
    return {
        "schema_version": handoff.schema_version,
        "handoff_id": handoff.handoff_id,
        "revision": handoff.revision,
        "content_digest": handoff.content_digest,
        "created_at": handoff.created_at.isoformat(),
        "content": handoff_content_to_dict(handoff.content),
    }


def handoff_from_dict(payload: Mapping[str, Any]) -> AgentHandoff:
    raw_content = cast(Mapping[str, Any], payload["content"])
    raw_producer = cast(Mapping[str, Any], raw_content["producer"])
    raw_consumer = raw_content.get("intended_consumer")
    raw_provenance = raw_content.get("provenance")
    provenance: Provenance | None = None
    if raw_provenance is not None:
        provenance_payload = cast(Mapping[str, Any], raw_provenance)
        provenance = Provenance(
            source=str(provenance_payload["source"]),
            actor_ref=(
                str(provenance_payload["actor_ref"])
                if provenance_payload.get("actor_ref") is not None
                else None
            ),
            details=cast(Mapping[str, Any], provenance_payload.get("details", {})),
        )
    raw_refs = cast(list[Mapping[str, Any]], raw_content.get("source_refs", []))
    source_refs = tuple(
        HandoffSourceRef(
            kind=HandoffSourceKind(str(ref["kind"])),
            resource_id=str(ref["resource_id"]),
            revision=str(ref["revision"]) if ref.get("revision") is not None else None,
            digest=str(ref["digest"]) if ref.get("digest") is not None else None,
        )
        for ref in raw_refs
    )
    content = HandoffContent(
        task_id=str(raw_content["task_id"]),
        plan_id=str(raw_content["plan_id"]),
        producer_step_id=str(raw_content["producer_step_id"]),
        consumer_step_id=str(raw_content["consumer_step_id"]),
        producer_run_id=str(raw_content["producer_run_id"]),
        producer=_participant_from_payload(raw_producer),
        intended_consumer=(
            _participant_from_payload(cast(Mapping[str, Any], raw_consumer))
            if raw_consumer is not None
            else None
        ),
        consumer_requirements=tuple(
            str(item) for item in cast(list[Any], raw_content.get("consumer_requirements", []))
        ),
        objective=str(raw_content["objective"]),
        completed_work_summary=str(raw_content["completed_work_summary"]),
        unresolved_questions=tuple(
            str(item) for item in cast(list[Any], raw_content.get("unresolved_questions", []))
        ),
        blockers=tuple(str(item) for item in cast(list[Any], raw_content.get("blockers", []))),
        assumptions=tuple(
            str(item) for item in cast(list[Any], raw_content.get("assumptions", []))
        ),
        constraints=tuple(
            str(item) for item in cast(list[Any], raw_content.get("constraints", []))
        ),
        source_refs=source_refs,
        recommended_next_action=str(raw_content["recommended_next_action"]),
        requested_output=str(raw_content["requested_output"]),
        provenance=provenance,
    )
    return AgentHandoff(
        handoff_id=str(payload["handoff_id"]),
        revision=int(payload["revision"]),
        content=content,
        content_digest=str(payload["content_digest"]),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        schema_version=str(payload["schema_version"]),
    )


def consumption_to_dict(consumption: HandoffConsumption) -> dict[str, Any]:
    return {
        "handoff_id": consumption.handoff_id,
        "handoff_revision": consumption.handoff_revision,
        "handoff_digest": consumption.handoff_digest,
        "consuming_run_id": consumption.consuming_run_id,
        "consumer": _participant_payload(consumption.consumer),
        "context_bundle_ref": (
            _source_ref_payload(consumption.context_bundle_ref)
            if consumption.context_bundle_ref is not None
            else None
        ),
        "consumed_at": consumption.consumed_at.isoformat(),
    }


def consumption_from_dict(payload: Mapping[str, Any]) -> HandoffConsumption:
    raw_context = payload.get("context_bundle_ref")
    context_ref: HandoffSourceRef | None = None
    if raw_context is not None:
        item = cast(Mapping[str, Any], raw_context)
        context_ref = HandoffSourceRef(
            kind=HandoffSourceKind(str(item["kind"])),
            resource_id=str(item["resource_id"]),
            revision=str(item["revision"]) if item.get("revision") is not None else None,
            digest=str(item["digest"]) if item.get("digest") is not None else None,
        )
    return HandoffConsumption(
        handoff_id=str(payload["handoff_id"]),
        handoff_revision=int(payload["handoff_revision"]),
        handoff_digest=str(payload["handoff_digest"]),
        consuming_run_id=str(payload["consuming_run_id"]),
        consumer=_participant_from_payload(cast(Mapping[str, Any], payload["consumer"])),
        context_bundle_ref=context_ref,
        consumed_at=datetime.fromisoformat(str(payload["consumed_at"])),
    )


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    raise TypeError(f"unsupported value in canonical handoff payload: {type(value).__name__}")
