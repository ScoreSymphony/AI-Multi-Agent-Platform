"""Application service for the canonical conversation interaction shell."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

from .models import (
    AgentSelectionRef,
    Conversation,
    ConversationContentBlock,
    ConversationMessage,
    ConversationParticipant,
    ConversationStatus,
    MessageRole,
    ModelRoutingPreference,
    ReferenceKind,
    ResourceReference,
)
from .repository import ConversationRepository

TaskCreator = Callable[[dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]]


class ConversationService:
    """Owns conversation semantics while delegating work to canonical platform services."""

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def create_conversation(
        self,
        *,
        title: str,
        owner_ref: str,
        summary: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        participants: Sequence[ConversationParticipant] = (),
        default_agent: AgentSelectionRef | None = None,
        model_preference: ModelRoutingPreference | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> Conversation:
        now = datetime.now(UTC)
        conversation = Conversation(
            title=title,
            summary=summary,
            owner_ref=owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            participants=tuple(participants),
            default_agent=default_agent,
            model_preference=model_preference,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        return await self._repository.create_conversation(conversation)

    async def get_conversation(self, conversation_id: str) -> Conversation:
        validate_id(conversation_id, "conversation")
        return await self._repository.get_conversation(conversation_id)

    async def get_message(self, message_id: str) -> ConversationMessage:
        validate_id(message_id, "message")
        return await self._repository.get_message(message_id)

    async def list_conversations(
        self,
        *,
        owner_ref: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        include_archived: bool = False,
    ) -> tuple[Conversation, ...]:
        statuses = (
            frozenset({ConversationStatus.OPEN, ConversationStatus.ARCHIVED})
            if include_archived
            else frozenset({ConversationStatus.OPEN})
        )
        return await self._repository.list_conversations(
            owner_ref=owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            statuses=statuses,
        )

    async def archive_conversation(self, conversation_id: str) -> Conversation:
        return await self._set_status(conversation_id, ConversationStatus.ARCHIVED)

    async def reopen_conversation(self, conversation_id: str) -> Conversation:
        return await self._set_status(conversation_id, ConversationStatus.OPEN)

    async def append_message(
        self,
        *,
        conversation_id: str,
        sender_ref: str,
        role: MessageRole,
        content: Sequence[ConversationContentBlock],
        references: Sequence[ResourceReference] = (),
        model_config_id: str | None = None,
        model_provider_ref: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> ConversationMessage:
        conversation = await self._repository.get_conversation(conversation_id)
        if conversation.status is not ConversationStatus.OPEN:
            raise ValueError("messages can only be appended to open conversations")
        message = ConversationMessage(
            conversation_id=conversation_id,
            sender_ref=sender_ref,
            role=role,
            content=tuple(content),
            references=tuple(references),
            model_config_id=model_config_id,
            model_provider_ref=model_provider_ref,
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata=metadata or {},
        )
        persisted = await self._repository.append_message(message)
        await self._repository.save_conversation(
            replace(conversation, updated_at=datetime.now(UTC))
        )
        return persisted

    async def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[tuple[ConversationMessage, ...], str | None]:
        return await self._repository.list_messages(
            conversation_id,
            limit=limit,
            cursor=cursor,
        )

    async def handoff_message_to_task(
        self,
        *,
        message_id: str,
        create_task: TaskCreator,
        task_payload: Mapping[str, JsonValue],
    ) -> tuple[ConversationMessage, Conversation, dict[str, JsonValue]]:
        """Create a canonical Task and persist only canonical references in chat state.

        ``create_task`` is expected to be the existing Control Plane Task creation path.
        The conversation layer never interprets or mirrors Task lifecycle state.
        """

        message = await self.get_message(message_id)
        conversation = await self.get_conversation(message.conversation_id)
        if conversation.status is ConversationStatus.TOMBSTONED:
            raise ValueError("cannot hand off a tombstoned conversation")

        payload = dict(task_payload)
        raw_metadata = payload.get("metadata", {})
        if not isinstance(raw_metadata, dict):
            raise ValueError("task payload metadata must be an object when supplied")
        task_metadata = dict(raw_metadata)
        task_metadata["conversation_id"] = conversation.id
        task_metadata["conversation_message_id"] = message.id
        payload["metadata"] = task_metadata
        task = await create_task(payload)
        task_id = task.get("id")
        if not isinstance(task_id, str):
            raise ValueError("canonical task creation did not return a task id")
        validate_id(task_id, "task")

        linked_conversation = await self.link_task(
            conversation_id=conversation.id,
            task_id=task_id,
            message_id=message.id,
        )
        linked_message = await self.get_message(message.id)
        return linked_message, linked_conversation, task

    async def link_task(
        self,
        *,
        conversation_id: str,
        task_id: str,
        message_id: str | None = None,
    ) -> Conversation:
        validate_id(task_id, "task")
        return await self._link_resource(
            conversation_id=conversation_id,
            reference=ResourceReference(kind=ReferenceKind.TASK, id=task_id),
            message_id=message_id,
        )

    async def link_run(
        self,
        *,
        conversation_id: str,
        run_id: str,
        message_id: str | None = None,
    ) -> Conversation:
        validate_id(run_id, "run")
        return await self._link_resource(
            conversation_id=conversation_id,
            reference=ResourceReference(kind=ReferenceKind.RUN, id=run_id),
            message_id=message_id,
        )

    async def link_artifact(
        self,
        *,
        conversation_id: str,
        artifact_id: str,
        message_id: str | None = None,
    ) -> Conversation:
        validate_id(artifact_id, "artifact")
        return await self._link_resource(
            conversation_id=conversation_id,
            reference=ResourceReference(kind=ReferenceKind.ARTIFACT, id=artifact_id),
            message_id=message_id,
        )

    async def _link_resource(
        self,
        *,
        conversation_id: str,
        reference: ResourceReference,
        message_id: str | None,
    ) -> Conversation:
        conversation = await self.get_conversation(conversation_id)
        if reference.kind is ReferenceKind.TASK:
            updated = replace(
                conversation,
                task_ids=tuple(dict.fromkeys((*conversation.task_ids, reference.id))),
                updated_at=datetime.now(UTC),
            )
        elif reference.kind is ReferenceKind.RUN:
            updated = replace(
                conversation,
                run_ids=tuple(dict.fromkeys((*conversation.run_ids, reference.id))),
                updated_at=datetime.now(UTC),
            )
        elif reference.kind is ReferenceKind.ARTIFACT:
            updated = replace(
                conversation,
                artifact_ids=tuple(dict.fromkeys((*conversation.artifact_ids, reference.id))),
                updated_at=datetime.now(UTC),
            )
        else:
            raise ValueError("unsupported conversation resource link")
        await self._repository.save_conversation(updated)
        if message_id is not None:
            message = await self.get_message(message_id)
            if message.conversation_id != conversation_id:
                raise ValueError("message does not belong to conversation")
            await self._repository.save_message(
                replace(message, references=_append_reference(message.references, reference))
            )
        return updated

    async def _set_status(
        self,
        conversation_id: str,
        status: ConversationStatus,
    ) -> Conversation:
        conversation = await self.get_conversation(conversation_id)
        if conversation.status is ConversationStatus.TOMBSTONED:
            raise ValueError("tombstoned conversations cannot change status")
        if conversation.status is status:
            return conversation
        updated = replace(conversation, status=status, updated_at=datetime.now(UTC))
        return await self._repository.save_conversation(updated)


def _append_reference(
    references: tuple[ResourceReference, ...],
    reference: ResourceReference,
) -> tuple[ResourceReference, ...]:
    if any(item.kind is reference.kind and item.id == reference.id for item in references):
        return references
    return (*references, reference)
