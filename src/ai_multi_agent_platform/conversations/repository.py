"""Durable platform-owned conversation repository for issue #72."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Protocol, cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import Conversation, ConversationMessage, ConversationStatus

_STORE_VERSION = 1


class ConversationNotFoundError(KeyError):
    pass


class ConversationRepository(Protocol):
    async def create_conversation(self, conversation: Conversation) -> Conversation: ...

    async def save_conversation(self, conversation: Conversation) -> Conversation: ...

    async def get_conversation(self, conversation_id: str) -> Conversation: ...

    async def list_conversations(
        self,
        *,
        owner_ref: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        statuses: frozenset[ConversationStatus] | None = None,
    ) -> tuple[Conversation, ...]: ...

    async def append_message(self, message: ConversationMessage) -> ConversationMessage: ...

    async def save_message(self, message: ConversationMessage) -> ConversationMessage: ...

    async def get_message(self, message_id: str) -> ConversationMessage: ...

    async def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[tuple[ConversationMessage, ...], str | None]: ...


class JsonConversationRepository:
    """Small deterministic durable reference repository.

    The JSON file stores only canonical conversation/message resources and references.
    It deliberately does not store model-provider chat sessions or alternate Task state.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()

    async def create_conversation(self, conversation: Conversation) -> Conversation:
        async with self._lock:
            store = self._load()
            conversations = self._conversation_map(store)
            if conversation.id in conversations:
                raise ValueError(f"conversation already exists: {conversation.id}")
            conversations[conversation.id] = conversation.to_json()
            self._write(store)
            return conversation

    async def save_conversation(self, conversation: Conversation) -> Conversation:
        async with self._lock:
            store = self._load()
            conversations = self._conversation_map(store)
            if conversation.id not in conversations:
                raise ConversationNotFoundError(conversation.id)
            conversations[conversation.id] = conversation.to_json()
            self._write(store)
            return conversation

    async def get_conversation(self, conversation_id: str) -> Conversation:
        async with self._lock:
            store = self._load()
            raw = self._conversation_map(store).get(conversation_id)
            if raw is None:
                raise ConversationNotFoundError(conversation_id)
            return Conversation.from_json(raw)

    async def list_conversations(
        self,
        *,
        owner_ref: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        statuses: frozenset[ConversationStatus] | None = None,
    ) -> tuple[Conversation, ...]:
        async with self._lock:
            store = self._load()
            conversations = [
                Conversation.from_json(raw) for raw in self._conversation_map(store).values()
            ]
        selected = [
            conversation
            for conversation in conversations
            if (owner_ref is None or conversation.owner_ref == owner_ref)
            and (project_id is None or conversation.project_id == project_id)
            and (workspace_id is None or conversation.workspace_id == workspace_id)
            and (statuses is None or conversation.status in statuses)
        ]
        selected.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return tuple(selected)

    async def append_message(self, message: ConversationMessage) -> ConversationMessage:
        async with self._lock:
            store = self._load()
            if message.conversation_id not in self._conversation_map(store):
                raise ConversationNotFoundError(message.conversation_id)
            messages = self._message_map(store)
            if message.id in messages:
                raise ValueError(f"message already exists: {message.id}")
            messages[message.id] = message.to_json()
            self._write(store)
            return message

    async def save_message(self, message: ConversationMessage) -> ConversationMessage:
        async with self._lock:
            store = self._load()
            messages = self._message_map(store)
            if message.id not in messages:
                raise KeyError(message.id)
            messages[message.id] = message.to_json()
            self._write(store)
            return message

    async def get_message(self, message_id: str) -> ConversationMessage:
        async with self._lock:
            raw = self._message_map(self._load()).get(message_id)
            if raw is None:
                raise KeyError(message_id)
            return ConversationMessage.from_json(raw)

    async def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[tuple[ConversationMessage, ...], str | None]:
        if limit < 1 or limit > 200:
            raise ValueError("message page limit must be between 1 and 200")
        offset = _decode_cursor(cursor)
        async with self._lock:
            store = self._load()
            if conversation_id not in self._conversation_map(store):
                raise ConversationNotFoundError(conversation_id)
            messages = [
                ConversationMessage.from_json(raw)
                for raw in self._message_map(store).values()
                if raw.get("conversation_id") == conversation_id
            ]
        messages.sort(key=lambda item: (item.created_at, item.id))
        page = tuple(messages[offset : offset + limit])
        next_offset = offset + len(page)
        next_cursor = str(next_offset) if next_offset < len(messages) else None
        return page, next_cursor

    def _load(self) -> dict[str, JsonValue]:
        if not self._path.exists():
            return {"version": _STORE_VERSION, "conversations": {}, "messages": {}}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("conversation store root must be an object")
        version = raw.get("version")
        if version != _STORE_VERSION:
            raise ValueError(f"unsupported conversation store version: {version!r}")
        if not isinstance(raw.get("conversations"), dict) or not isinstance(
            raw.get("messages"), dict
        ):
            raise ValueError("conversation store collections are invalid")
        return cast(dict[str, JsonValue], raw)

    def _write(self, store: dict[str, JsonValue]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        temporary.write_text(
            json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self._path)

    @staticmethod
    def _conversation_map(store: dict[str, JsonValue]) -> dict[str, dict[str, JsonValue]]:
        value = store["conversations"]
        if not isinstance(value, dict):
            raise ValueError("conversation store conversations must be an object")
        return cast(dict[str, dict[str, JsonValue]], value)

    @staticmethod
    def _message_map(store: dict[str, JsonValue]) -> dict[str, dict[str, JsonValue]]:
        value = store["messages"]
        if not isinstance(value, dict):
            raise ValueError("conversation store messages must be an object")
        return cast(dict[str, dict[str, JsonValue]], value)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise ValueError("message cursor must be a non-negative integer") from exc
    if offset < 0:
        raise ValueError("message cursor must be a non-negative integer")
    return offset
