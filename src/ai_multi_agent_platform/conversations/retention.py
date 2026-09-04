"""Retention, tombstone and export semantics for canonical Conversations (#72)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    ContentKind,
    Conversation,
    ConversationContentBlock,
    ConversationMessage,
    ConversationStatus,
    MessageStatus,
)
from .service import ConversationService

RETENTION_METADATA_KEY = "conversation_retention"
TOMBSTONED_AT_METADATA_KEY = "conversation_tombstoned_at"
RESERVED_CONVERSATION_METADATA_KEYS = frozenset(
    {RETENTION_METADATA_KEY, TOMBSTONED_AT_METADATA_KEY}
)


class ConversationRetentionMode(StrEnum):
    DURABLE = "durable"
    UNTIL = "until"


@dataclass(frozen=True, slots=True)
class ConversationRetentionPolicy:
    mode: ConversationRetentionMode = ConversationRetentionMode.DURABLE
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.mode is ConversationRetentionMode.DURABLE:
            if self.expires_at is not None:
                raise ValueError("durable conversation retention must not define expires_at")
            return
        if self.expires_at is None:
            raise ValueError("until conversation retention requires expires_at")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("conversation retention expires_at must include a timezone offset")
        object.__setattr__(self, "expires_at", self.expires_at.astimezone(UTC))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "mode": self.mode.value,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
        }

    @classmethod
    def from_json(cls, value: object) -> ConversationRetentionPolicy:
        if not isinstance(value, Mapping):
            raise ValueError("conversation retention must be an object")
        mode_raw = value.get("mode", ConversationRetentionMode.DURABLE.value)
        if not isinstance(mode_raw, str):
            raise ValueError("conversation retention mode must be a string")
        try:
            mode = ConversationRetentionMode(mode_raw)
        except ValueError as exc:
            raise ValueError("unsupported conversation retention mode") from exc
        expires_raw = value.get("expires_at")
        expires_at: datetime | None = None
        if expires_raw is not None:
            if not isinstance(expires_raw, str) or not expires_raw.strip():
                raise ValueError("conversation retention expires_at must be an ISO-8601 string")
            try:
                expires_at = datetime.fromisoformat(expires_raw)
            except ValueError as exc:
                raise ValueError("conversation retention expires_at must be ISO-8601") from exc
        return cls(mode=mode, expires_at=expires_at)


class ConversationRetentionManager:
    """Own Conversation retention without touching canonical Task/Run/Event state."""

    def __init__(self, service: ConversationService) -> None:
        self._service = service

    def policy_for(self, conversation: Conversation) -> ConversationRetentionPolicy:
        raw = conversation.metadata.get(RETENTION_METADATA_KEY)
        if raw is None:
            return ConversationRetentionPolicy()
        return ConversationRetentionPolicy.from_json(raw)

    async def set_policy(
        self,
        conversation_id: str,
        policy: ConversationRetentionPolicy,
    ) -> Conversation:
        conversation = await self._service.get_conversation(conversation_id)
        if conversation.status is ConversationStatus.TOMBSTONED:
            raise ValueError("tombstoned conversations cannot change retention")
        metadata = dict(conversation.metadata)
        metadata[RETENTION_METADATA_KEY] = policy.to_json()
        metadata.pop(TOMBSTONED_AT_METADATA_KEY, None)
        updated = replace(
            conversation,
            metadata=metadata,
            updated_at=datetime.now(UTC),
        )
        return await self._service._repository.save_conversation(updated)

    async def tombstone(
        self,
        conversation_id: str,
        *,
        now: datetime | None = None,
    ) -> Conversation:
        conversation = await self._service.get_conversation(conversation_id)
        if conversation.status is ConversationStatus.TOMBSTONED:
            return conversation
        tombstoned_at = _normalized_now(now)
        policy = self.policy_for(conversation)

        for message in await self._all_messages(conversation.id):
            if message.status is MessageStatus.TOMBSTONED:
                continue
            await self._service._repository.save_message(_tombstone_message(message, tombstoned_at))

        retained_metadata: dict[str, JsonValue] = {
            RETENTION_METADATA_KEY: policy.to_json(),
            TOMBSTONED_AT_METADATA_KEY: tombstoned_at.isoformat(),
        }
        tombstoned = replace(
            conversation,
            title="Deleted conversation",
            summary=None,
            participants=(),
            status=ConversationStatus.TOMBSTONED,
            default_agent=None,
            model_preference=None,
            updated_at=tombstoned_at,
            metadata=retained_metadata,
        )
        return await self._service._repository.save_conversation(tombstoned)

    async def expire_due(self, *, now: datetime | None = None) -> tuple[str, ...]:
        current = _normalized_now(now)
        candidates = await self._service._repository.list_conversations(
            statuses=frozenset({ConversationStatus.OPEN, ConversationStatus.ARCHIVED})
        )
        expired: list[str] = []
        for conversation in candidates:
            policy = self.policy_for(conversation)
            if (
                policy.mode is ConversationRetentionMode.UNTIL
                and policy.expires_at is not None
                and policy.expires_at <= current
            ):
                await self.tombstone(conversation.id, now=current)
                expired.append(conversation.id)
        return tuple(expired)

    async def export(self, conversation_id: str) -> dict[str, JsonValue]:
        conversation = await self._service.get_conversation(conversation_id)
        messages = await self._all_messages(conversation.id)
        return {
            "schema_version": "1",
            "type": "conversation-export",
            "id": conversation.id,
            "conversation": conversation.to_json(),
            "messages": [message.to_json() for message in messages],
            "retention": self.policy_for(conversation).to_json(),
            "external_resources": {
                "expanded": False,
                "task_ids": list(conversation.task_ids),
                "run_ids": list(conversation.run_ids),
                "artifact_ids": list(conversation.artifact_ids),
                "file_content_included": False,
                "knowledge_content_included": False,
                "canonical_event_history_included": False,
            },
            "memory": {
                "included": False,
                "automatic_promotion": False,
            },
        }

    async def _all_messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]:
        messages: list[ConversationMessage] = []
        cursor: str | None = None
        while True:
            page, cursor = await self._service.list_messages(
                conversation_id,
                limit=200,
                cursor=cursor,
            )
            messages.extend(page)
            if cursor is None:
                break
        return tuple(messages)


def _tombstone_message(
    message: ConversationMessage, tombstoned_at: datetime
) -> ConversationMessage:
    marker = ConversationContentBlock(
        kind=ContentKind.JSON,
        value=cast(JsonValue, {"tombstoned": True}),
    )
    return replace(
        message,
        content=(marker,),
        references=(),
        model_config_id=None,
        model_provider_ref=None,
        edited_at=tombstoned_at,
        status=MessageStatus.TOMBSTONED,
        revision=message.revision + 1,
        metadata={},
    )


def _normalized_now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("retention time must include a timezone offset")
    return current.astimezone(UTC)


__all__ = [
    "ConversationRetentionManager",
    "ConversationRetentionMode",
    "ConversationRetentionPolicy",
    "RESERVED_CONVERSATION_METADATA_KEYS",
    "RETENTION_METADATA_KEY",
    "TOMBSTONED_AT_METADATA_KEY",
]
