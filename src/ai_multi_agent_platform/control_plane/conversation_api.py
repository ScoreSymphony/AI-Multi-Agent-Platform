"""Canonical Control Plane binding for task-centric conversations (issue #72)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, cast

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, OperationControl
from ai_multi_agent_platform.conversations import (
    AgentSelectionRef,
    Conversation,
    ConversationContentBlock,
    ConversationMessage,
    ConversationParticipant,
    ConversationService,
    MessageRole,
    ModelRoutingPreference,
    ParticipantKind,
    ReferenceKind,
    ResourceReference,
)
from ai_multi_agent_platform.data import DataAccessContext, FileProvider

from .extensions import CommandHandler, ResourceService
from .models import PageQuery, RequestContext
from .service import _payload_digest

CONVERSATION_COLLECTION = "conversations"
CONVERSATION_MESSAGE_COLLECTION = "conversation-messages"
CONVERSATION_COLLECTIONS = (CONVERSATION_COLLECTION, CONVERSATION_MESSAGE_COLLECTION)
CONVERSATION_COMMANDS = (
    "conversation.create",
    "conversation.archive",
    "conversation.reopen",
    "conversation.message.add",
    "conversation.message.create-task",
    "conversation.message.attach-task",
    "conversation.link-run",
    "conversation.link-artifact",
)


class ConversationControlPlane(Protocol):
    """Minimal current Control Plane surface required by the conversation binder."""

    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> None: ...

    async def _allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> bool: ...

    async def get_project(
        self, context: RequestContext, project_id: str
    ) -> dict[str, JsonValue]: ...

    async def get_workspace(
        self, context: RequestContext, workspace_id: str
    ) -> dict[str, JsonValue]: ...

    async def get_task(self, context: RequestContext, task_id: str) -> dict[str, JsonValue]: ...

    async def get_run(
        self,
        context: RequestContext,
        run_id: str,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]: ...

    async def get_reference(
        self,
        context: RequestContext,
        collection: Literal["plans", "steps", "artifacts", "results"],
        resource_id: str,
    ) -> dict[str, JsonValue]: ...

    async def get_model(
        self, context: RequestContext, model_id_or_alias: str
    ) -> dict[str, JsonValue]: ...

    async def get_model_provider(
        self, context: RequestContext, provider_id: str
    ) -> dict[str, JsonValue]: ...

    async def create_task(
        self, context: RequestContext, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]: ...

    def register_resource_service(self, collection: str, service: ResourceService) -> None: ...

    def register_command(self, command: str, handler: CommandHandler) -> None: ...


class ConversationResourceService(ResourceService):
    def __init__(
        self,
        service: ConversationService,
        control_plane: ConversationControlPlane,
    ) -> None:
        self._service = service
        self._control_plane = control_plane

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        filters = query.filters or {}
        include_archived = _optional_bool_filter(filters, "include_archived")
        items = await self._service.list_conversations(
            owner_ref=filters.get("owner_ref"),
            project_id=filters.get("project_id"),
            workspace_id=filters.get("workspace_id"),
            include_archived=include_archived,
        )
        visible: list[dict[str, JsonValue]] = []
        for conversation in items:
            if await _conversation_allowed(
                self._control_plane,
                context,
                "conversation:list",
                conversation,
            ):
                visible.append(_conversation_resource(conversation))
        return tuple(visible)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        conversation = await self._service.get_conversation(resource_id)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation:read",
            conversation,
        )
        return _conversation_resource(conversation)


class ConversationMessageResourceService(ResourceService):
    def __init__(
        self,
        service: ConversationService,
        control_plane: ConversationControlPlane,
    ) -> None:
        self._service = service
        self._control_plane = control_plane

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        filters = query.filters or {}
        conversation_id = filters.get("conversation_id")
        if conversation_id is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "conversation-messages listing requires filter[conversation_id]",
                details={"filter": "conversation_id"},
            )
        conversation = await self._service.get_conversation(conversation_id)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation-message:list",
            conversation,
        )
        # The generic Control Plane owns northbound pagination, while the repository
        # owns durable history pagination. Drain repository pages so the generic cursor
        # never truncates histories after an arbitrary first page.
        messages: list[ConversationMessage] = []
        cursor: str | None = None
        while True:
            page, cursor = await self._service.list_messages(
                conversation_id, limit=200, cursor=cursor
            )
            messages.extend(page)
            if cursor is None:
                break
        return tuple(_message_resource(message) for message in messages)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        message = await self._service._repository.get_message(resource_id)
        conversation = await self._service.get_conversation(message.conversation_id)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation-message:read",
            conversation,
            resource_ref=message.id,
        )
        return _message_resource(message)


class ConversationCommandHandlers:
    """Mutating conversation commands using only canonical platform boundaries."""

    def __init__(
        self,
        service: ConversationService,
        control_plane: ConversationControlPlane,
        *,
        agent_service: AgentService | None = None,
        file_provider: FileProvider | None = None,
    ) -> None:
        self._service = service
        self._control_plane = control_plane
        self._agent_service = agent_service
        self._file_provider = file_provider

    async def create_conversation(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != CONVERSATION_COLLECTION:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "conversation.create resource_ref must be 'conversations'",
            )
        project_id = _optional_string(payload, "project_id")
        workspace_id = _optional_string(payload, "workspace_id")
        if project_id is not None:
            await self._control_plane.get_project(context, project_id)
        if workspace_id is not None:
            workspace = await self._control_plane.get_workspace(context, workspace_id)
            workspace_project = workspace.get("project_id")
            if project_id is None and isinstance(workspace_project, str):
                project_id = workspace_project
            elif project_id is not None and workspace_project != project_id:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "workspace does not belong to conversation project",
                )

        target = _optional_mapping(payload, "target")
        default_agent = _optional_agent_selection(payload.get("default_agent"))
        metadata = _optional_json_mapping(payload, "metadata")
        if target is not None:
            project_id, default_agent, target_metadata = await self._resolve_target(
                context,
                target,
                project_id=project_id,
                default_agent=default_agent,
            )
            metadata = {**metadata, "target": target_metadata}

        if project_id is not None:
            await self._control_plane.get_project(context, project_id)

        participants = _participants(payload.get("participants"))
        for participant in participants:
            participant_project = await self._validate_participant(context, participant)
            _require_same_project(project_id, participant_project, "participant")
        if default_agent is not None:
            agent_project = await self._validate_agent_selection(context, default_agent)
            project_id = _merge_project(project_id, agent_project, "default agent")

        model_preference = _optional_model_preference(payload.get("model_preference"))
        if model_preference is not None and model_preference.model_config_id is not None:
            await self._control_plane.get_model(context, model_preference.model_config_id)

        await self._control_plane._authorize(
            context,
            "conversation:create",
            CONVERSATION_COLLECTION,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            project_id=project_id,
            request_payload_digest=_payload_digest(payload),
        )
        conversation = await self._service.create_conversation(
            title=_required_string(payload, "title"),
            owner_ref=context.actor.principal_ref,
            summary=_optional_string(payload, "summary"),
            project_id=project_id,
            workspace_id=workspace_id,
            participants=participants,
            default_agent=default_agent,
            model_preference=model_preference,
            metadata=metadata,
        )
        return _conversation_resource(conversation)

    async def archive_conversation(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_empty(payload)
        conversation = await self._service.get_conversation(resource_ref)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation:modify",
            conversation,
            request_payload_digest=_payload_digest(payload),
        )
        return _conversation_resource(await self._service.archive_conversation(resource_ref))

    async def reopen_conversation(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_empty(payload)
        conversation = await self._service.get_conversation(resource_ref)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation:modify",
            conversation,
            request_payload_digest=_payload_digest(payload),
        )
        return _conversation_resource(await self._service.reopen_conversation(resource_ref))

    async def add_message(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        conversation = await self._service.get_conversation(resource_ref)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation-message:create",
            conversation,
            request_payload_digest=_payload_digest(payload),
        )
        if "sender_ref" in payload:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "sender_ref is established by authentication and must not be supplied",
            )
        try:
            role = MessageRole(_optional_string(payload, "role") or MessageRole.USER.value)
        except ValueError as exc:
            raise ContractError(ErrorCode.INVALID_REQUEST, "unsupported message role") from exc
        if role is not MessageRole.USER:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "northbound conversation messages must use the authenticated user role",
            )
        references = _references(payload.get("references"))
        content = _content_blocks(payload.get("content"))
        for reference in _all_references(content, references):
            await self._validate_reference(context, conversation, reference)

        model_config_id = _optional_string(payload, "model_config_id")
        if model_config_id is not None:
            await self._control_plane.get_model(context, model_config_id)
        model_provider_ref = _optional_string(payload, "model_provider_ref")
        if model_provider_ref is not None:
            await self._control_plane.get_model_provider(context, model_provider_ref)
        if model_config_id is not None and model_provider_ref is not None:
            model = await self._control_plane.get_model(context, model_config_id)
            if model.get("provider_id") != model_provider_ref:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "model_provider_ref does not own model_config_id",
                )

        message = await self._service.append_message(
            conversation_id=conversation.id,
            sender_ref=context.actor.principal_ref,
            role=role,
            content=content,
            references=references,
            model_config_id=model_config_id,
            model_provider_ref=model_provider_ref,
            correlation_id=context.correlation_id,
            causation_id=context.idempotency_key,
            metadata=_optional_json_mapping(payload, "metadata"),
        )
        return _message_resource(message)

    async def create_task_from_message(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        message = await self._service._repository.get_message(resource_ref)
        conversation = await self._service.get_conversation(message.conversation_id)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation-message:modify",
            conversation,
            resource_ref=message.id,
            request_payload_digest=_payload_digest(payload),
        )
        task_payload = dict(payload)
        if "project_id" not in task_payload and conversation.project_id is not None:
            task_payload["project_id"] = conversation.project_id

        async def create_task(task: dict[str, JsonValue]) -> dict[str, JsonValue]:
            return await self._control_plane.create_task(context, task)

        linked_message, linked_conversation, task = await self._service.handoff_message_to_task(
            message_id=message.id,
            create_task=create_task,
            task_payload=task_payload,
        )
        return {
            "id": task["id"],
            "type": "conversation-task-handoff",
            "conversation": _conversation_resource(linked_conversation),
            "message": _message_resource(linked_message),
            "task": task,
        }

    async def attach_message_to_task(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        message = await self._service._repository.get_message(resource_ref)
        conversation = await self._service.get_conversation(message.conversation_id)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation-message:modify",
            conversation,
            resource_ref=message.id,
            request_payload_digest=_payload_digest(payload),
        )
        task_id = _required_string(payload, "task_id")
        task = await self._control_plane.get_task(context, task_id)
        _require_same_project(conversation.project_id, task.get("project_id"), "task")
        reference = ResourceReference(kind=ReferenceKind.TASK, id=task_id)
        linked_message = await self._service._repository.save_message(
            _message_with_reference(message, reference)
        )
        return {
            "id": linked_message.id,
            "type": "conversation-task-attachment",
            "conversation_id": conversation.id,
            "message": _message_resource(linked_message),
            "task": task,
        }

    async def link_run(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        conversation = await self._service.get_conversation(resource_ref)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation:modify",
            conversation,
            request_payload_digest=_payload_digest(payload),
        )
        run_id = _required_string(payload, "run_id")
        run = await self._control_plane.get_run(context, run_id)
        _require_same_project(conversation.project_id, run.get("project_id"), "run")
        updated = await self._service.link_run(
            conversation_id=conversation.id,
            run_id=run_id,
            message_id=_optional_string(payload, "message_id"),
        )
        return _conversation_resource(updated)

    async def link_artifact(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        conversation = await self._service.get_conversation(resource_ref)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation:modify",
            conversation,
            request_payload_digest=_payload_digest(payload),
        )
        artifact_id = _required_string(payload, "artifact_id")
        artifact = await self._control_plane.get_reference(context, "artifacts", artifact_id)
        task_id = artifact.get("task_id")
        if isinstance(task_id, str):
            task = await self._control_plane.get_task(context, task_id)
            _require_same_project(conversation.project_id, task.get("project_id"), "artifact")
        updated = await self._service.link_artifact(
            conversation_id=conversation.id,
            artifact_id=artifact_id,
            message_id=_optional_string(payload, "message_id"),
        )
        return _conversation_resource(updated)

    async def _resolve_target(
        self,
        context: RequestContext,
        target: Mapping[str, JsonValue],
        *,
        project_id: str | None,
        default_agent: AgentSelectionRef | None,
    ) -> tuple[str | None, AgentSelectionRef | None, dict[str, JsonValue]]:
        kind = _required_string(dict(target), "kind")
        target_id = _required_string(dict(target), "id")
        revision = _optional_positive_int(dict(target), "revision")
        if kind == "agent":
            selection = AgentSelectionRef(
                kind=ParticipantKind.AGENT, id=target_id, revision=revision
            )
            agent_project = await self._validate_agent_selection(context, selection)
            project_id = _merge_project(project_id, agent_project, "agent")
            default_agent = selection
        elif kind == "agent_team":
            selection = AgentSelectionRef(
                kind=ParticipantKind.AGENT_TEAM,
                id=target_id,
                revision=revision,
            )
            team_project = await self._validate_agent_selection(context, selection)
            project_id = _merge_project(project_id, team_project, "agent team")
            default_agent = selection
        elif kind == "project":
            if revision is not None:
                raise ContractError(ErrorCode.INVALID_REQUEST, "project target has no revision")
            await self._control_plane.get_project(context, target_id)
            project_id = _merge_project(project_id, target_id, "project")
        elif kind == "task":
            if revision is not None:
                raise ContractError(ErrorCode.INVALID_REQUEST, "task target has no revision")
            task = await self._control_plane.get_task(context, target_id)
            raw_project = task.get("project_id")
            task_project = raw_project if isinstance(raw_project, str) else None
            project_id = _merge_project(project_id, task_project, "task")
        elif kind == "orchestrator":
            if target_id != "platform" or revision is not None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "orchestrator target must use the canonical platform entrypoint",
                )
        else:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, f"unsupported conversation target: {kind}"
            )
        result: dict[str, JsonValue] = {"kind": kind, "id": target_id}
        if revision is not None:
            result["revision"] = revision
        return project_id, default_agent, result

    async def _validate_participant(
        self,
        context: RequestContext,
        participant: ConversationParticipant,
    ) -> str | None:
        if participant.kind not in {ParticipantKind.AGENT, ParticipantKind.AGENT_TEAM}:
            return None
        return await self._validate_agent_selection(
            context,
            AgentSelectionRef(kind=participant.kind, id=participant.id),
        )

    async def _validate_agent_selection(
        self,
        context: RequestContext,
        selection: AgentSelectionRef,
    ) -> str | None:
        service = self._agent_service
        if service is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical Agent service is not configured for conversation routing",
                retryable=True,
            )
        if selection.kind is ParticipantKind.AGENT:
            revision = service.get_agent_revision(selection.id, selection.revision)
            await self._control_plane._authorize(
                context,
                "agent:read",
                selection.id,
                owner_type=revision.owner_ref.type,
                owner_id=revision.owner_ref.id,
                project_id=revision.project_id,
            )
            return revision.project_id
        team_revision = service.get_team_revision(selection.id, selection.revision)
        await self._control_plane._authorize(
            context,
            "agent-team:read",
            selection.id,
            owner_type=team_revision.owner_ref.type,
            owner_id=team_revision.owner_ref.id,
            project_id=team_revision.project_id,
        )
        return team_revision.project_id

    async def _validate_reference(
        self,
        context: RequestContext,
        conversation: Conversation,
        reference: ResourceReference,
    ) -> None:
        if reference.kind is ReferenceKind.FILE:
            provider = self._file_provider
            if provider is None:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "canonical File provider is not configured for conversation attachments",
                    retryable=True,
                )
            record = await provider.get_file(
                reference.id,
                DataAccessContext(
                    operation=OperationContext(
                        correlation_id=context.correlation_id,
                        owner_type=context.actor.owner_type,
                        owner_id=context.actor.owner_id,
                        project_id=conversation.project_id,
                        control=OperationControl(idempotency_key=context.idempotency_key),
                    ),
                    actor_ref=context.actor.principal_ref,
                    audit_metadata=dict(context.actor.trust_context),
                ),
            )
            _require_same_project(conversation.project_id, record.project_id, "file")
            return
        if reference.kind is ReferenceKind.ARTIFACT:
            artifact = await self._control_plane.get_reference(context, "artifacts", reference.id)
            task_id = artifact.get("task_id")
            if isinstance(task_id, str):
                task = await self._control_plane.get_task(context, task_id)
                _require_same_project(conversation.project_id, task.get("project_id"), "artifact")
            return
        if reference.kind is ReferenceKind.TASK:
            task = await self._control_plane.get_task(context, reference.id)
            _require_same_project(conversation.project_id, task.get("project_id"), "task")
            return
        if reference.kind is ReferenceKind.RUN:
            run = await self._control_plane.get_run(context, reference.id)
            _require_same_project(conversation.project_id, run.get("project_id"), "run")
            return
        if reference.kind is ReferenceKind.RESULT:
            result = await self._control_plane.get_reference(context, "results", reference.id)
            task_id = result.get("task_id")
            if isinstance(task_id, str):
                task = await self._control_plane.get_task(context, task_id)
                _require_same_project(conversation.project_id, task.get("project_id"), "result")
            return
        if reference.kind is ReferenceKind.AGENT:
            await self._validate_agent_selection(
                context,
                AgentSelectionRef(kind=ParticipantKind.AGENT, id=reference.id),
            )
            return
        if reference.kind is ReferenceKind.AGENT_TEAM:
            await self._validate_agent_selection(
                context,
                AgentSelectionRef(kind=ParticipantKind.AGENT_TEAM, id=reference.id),
            )
            return
        raise ContractError(ErrorCode.INVALID_REQUEST, "unsupported conversation reference")


def register_conversation_control_plane(
    control_plane: ConversationControlPlane,
    service: ConversationService,
    *,
    agent_service: AgentService | None = None,
    file_provider: FileProvider | None = None,
) -> None:
    """Register canonical conversation resources/commands on the current Control Plane."""

    control_plane.register_resource_service(
        CONVERSATION_COLLECTION,
        ConversationResourceService(service, control_plane),
    )
    control_plane.register_resource_service(
        CONVERSATION_MESSAGE_COLLECTION,
        ConversationMessageResourceService(service, control_plane),
    )
    handlers = ConversationCommandHandlers(
        service,
        control_plane,
        agent_service=agent_service,
        file_provider=file_provider,
    )
    registrations: dict[str, CommandHandler] = {
        "conversation.create": handlers.create_conversation,
        "conversation.archive": handlers.archive_conversation,
        "conversation.reopen": handlers.reopen_conversation,
        "conversation.message.add": handlers.add_message,
        "conversation.message.create-task": handlers.create_task_from_message,
        "conversation.message.attach-task": handlers.attach_message_to_task,
        "conversation.link-run": handlers.link_run,
        "conversation.link-artifact": handlers.link_artifact,
    }
    for command, handler in registrations.items():
        control_plane.register_command(command, handler)


def _conversation_resource(conversation: Conversation) -> dict[str, JsonValue]:
    resource = conversation.to_json()
    resource["type"] = "conversation"
    return resource


def _message_resource(message: ConversationMessage) -> dict[str, JsonValue]:
    resource = message.to_json()
    resource["type"] = "conversation-message"
    return resource


async def _authorize_conversation(
    control_plane: ConversationControlPlane,
    context: RequestContext,
    action: str,
    conversation: Conversation,
    *,
    resource_ref: str | None = None,
    request_payload_digest: str | None = None,
) -> None:
    _require_private_owner_or_project(context, conversation)
    await control_plane._authorize(
        context,
        action,
        resource_ref or conversation.id,
        project_id=conversation.project_id,
        request_payload_digest=request_payload_digest,
    )


async def _conversation_allowed(
    control_plane: ConversationControlPlane,
    context: RequestContext,
    action: str,
    conversation: Conversation,
) -> bool:
    if conversation.project_id is None and conversation.owner_ref != context.actor.principal_ref:
        return False
    return await control_plane._allowed(
        context,
        action,
        conversation.id,
        project_id=conversation.project_id,
    )


def _require_private_owner_or_project(context: RequestContext, conversation: Conversation) -> None:
    if conversation.project_id is None and conversation.owner_ref != context.actor.principal_ref:
        raise ContractError(ErrorCode.FORBIDDEN, "private conversation belongs to another actor")


def _participants(value: JsonValue | None) -> tuple[ConversationParticipant, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "participants must be an array")
    try:
        return tuple(ConversationParticipant.from_json(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


def _optional_agent_selection(value: JsonValue | None) -> AgentSelectionRef | None:
    if value is None:
        return None
    try:
        return AgentSelectionRef.from_json(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


def _optional_model_preference(value: JsonValue | None) -> ModelRoutingPreference | None:
    if value is None:
        return None
    try:
        return ModelRoutingPreference.from_json(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


def _references(value: JsonValue | None) -> tuple[ResourceReference, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "references must be an array")
    try:
        return tuple(ResourceReference.from_json(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


def _content_blocks(value: JsonValue | None) -> tuple[ConversationContentBlock, ...]:
    if not isinstance(value, list) or not value:
        raise ContractError(ErrorCode.INVALID_REQUEST, "content must be a non-empty array")
    try:
        return tuple(ConversationContentBlock.from_json(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


def _all_references(
    content: Sequence[ConversationContentBlock],
    references: Sequence[ResourceReference],
) -> tuple[ResourceReference, ...]:
    combined = list(references)
    combined.extend(block.reference for block in content if block.reference is not None)
    unique: dict[tuple[ReferenceKind, str], ResourceReference] = {}
    for reference in combined:
        unique[(reference.kind, reference.id)] = reference
    return tuple(unique.values())


def _message_with_reference(
    message: ConversationMessage,
    reference: ResourceReference,
) -> ConversationMessage:
    from dataclasses import replace

    if any(item.kind is reference.kind and item.id == reference.id for item in message.references):
        return message
    return replace(message, references=(*message.references, reference))


def _merge_project(current: str | None, candidate: str | None, label: str) -> str | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    if current != candidate:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{label} target belongs to a different project",
        )
    return current


def _require_same_project(
    conversation_project: str | None,
    resource_project: JsonValue | str | None,
    label: str,
) -> None:
    if conversation_project is None or resource_project is None:
        return
    if not isinstance(resource_project, str) or resource_project != conversation_project:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            f"{label} reference crosses the conversation project boundary",
        )


def _required_string(payload: Mapping[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a non-blank string",
            details={"field": name},
        )
    return value


def _optional_string(payload: Mapping[str, JsonValue], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a non-blank string",
            details={"field": name},
        )
    return value


def _optional_mapping(
    payload: Mapping[str, JsonValue], name: str
) -> Mapping[str, JsonValue] | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be an object")
    return cast(Mapping[str, JsonValue], value)


def _optional_json_mapping(payload: Mapping[str, JsonValue], name: str) -> dict[str, JsonValue]:
    value = payload.get(name)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be an object")
    return {str(key): item for key, item in value.items()}


def _optional_positive_int(payload: Mapping[str, JsonValue], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a positive integer")
    return value


def _optional_bool_filter(filters: Mapping[str, str], name: str) -> bool:
    value = filters.get(name)
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ContractError(ErrorCode.INVALID_REQUEST, f"filter[{name}] must be boolean")


def _require_empty(payload: Mapping[str, JsonValue]) -> None:
    if payload:
        raise ContractError(ErrorCode.INVALID_REQUEST, "command does not accept a payload")
