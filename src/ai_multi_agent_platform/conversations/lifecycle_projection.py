"""Project canonical Task lifecycle events into Conversation context without owning lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

from .models import ReferenceKind, ResourceReference
from .service import ConversationService


@dataclass(frozen=True, slots=True)
class ConversationLifecycleProjection:
    """Conversation-facing metadata derived only from one canonical Task event."""

    references: tuple[ResourceReference, ...] = ()
    attention: dict[str, JsonValue] | None = None


async def project_conversation_lifecycle_event(
    service: ConversationService,
    *,
    conversation_id: str,
    task_id: str,
    event: Mapping[str, JsonValue],
) -> ConversationLifecycleProjection:
    """Materialize safe canonical links and attention from an authoritative Task event.

    The source event remains the authority. This helper never creates or mutates a Task/Run,
    never interprets model text and only stores already-canonical Run/Artifact links in the
    Conversation repository.
    """

    validate_id(conversation_id, "conversation")
    validate_id(task_id, "task")
    event_type = _required_string(event, "event_type")
    subject_type = _optional_string(event, "subject_type")
    subject_id = _optional_string(event, "subject_id")
    payload = _payload(event)

    references: list[ResourceReference] = []

    if subject_type == "run" and subject_id is not None and event_type.startswith("run."):
        validate_id(subject_id, "run")
        await service.link_run(conversation_id=conversation_id, run_id=subject_id)
        references.append(ResourceReference(kind=ReferenceKind.RUN, id=subject_id))

    if event_type == "artifact.attached":
        artifact_id = _required_string(payload, "artifact_id")
        validate_id(artifact_id, "artifact")
        await service.link_artifact(conversation_id=conversation_id, artifact_id=artifact_id)
        references.append(ResourceReference(kind=ReferenceKind.ARTIFACT, id=artifact_id))

    if event_type == "result.attached":
        result_id = _required_string(payload, "result_id")
        validate_id(result_id, "result")
        references.append(ResourceReference(kind=ReferenceKind.RESULT, id=result_id))

    attention = _waiting_attention(task_id, event_type, payload)
    return ConversationLifecycleProjection(
        references=_deduplicate_references(references),
        attention=attention,
    )


def _waiting_attention(
    task_id: str,
    event_type: str,
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue] | None:
    if event_type != "task.waiting":
        return None
    attention: dict[str, JsonValue] = {
        "kind": "task_waiting",
        "task_id": task_id,
        "blocked": payload.get("blocked") is True,
    }
    reason = payload.get("reason")
    if isinstance(reason, str) and reason.strip():
        attention["reason"] = reason
    verification_state = payload.get("verification_state")
    if isinstance(verification_state, str) and verification_state.strip():
        attention["verification_state"] = verification_state
    blocking = payload.get("blocking_verification_ids")
    if isinstance(blocking, list) and all(isinstance(item, str) for item in blocking):
        attention["blocking_verification_ids"] = list(blocking)
    return attention


def _payload(event: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    value = event.get("payload")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "canonical event payload is not an object")
    return value


def _required_string(value: Mapping[str, JsonValue], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"canonical event {key} is not a non-blank string",
        )
    return raw


def _optional_string(value: Mapping[str, JsonValue], key: str) -> str | None:
    raw = value.get(key)
    return raw if isinstance(raw, str) and raw.strip() else None


def _deduplicate_references(
    references: list[ResourceReference],
) -> tuple[ResourceReference, ...]:
    seen: set[tuple[ReferenceKind, str]] = set()
    unique: list[ResourceReference] = []
    for reference in references:
        key = (reference.kind, reference.id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(reference)
    return tuple(unique)


__all__ = [
    "ConversationLifecycleProjection",
    "project_conversation_lifecycle_event",
]
