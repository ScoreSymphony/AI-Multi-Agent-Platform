"""Compose canonical conversations on top of the current Control Plane."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, cast

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import ConversationService
from ai_multi_agent_platform.data import FileProvider
from ai_multi_agent_platform.domain import TaskStatus, validate_id

from .automation_runtime_composition import ControlPlane as _CurrentControlPlane
from .automation_runtime_composition import ControlPlaneASGI
from .automation_runtime_composition import ControlPlaneHTTP as _CurrentControlPlaneHTTP
from .automation_runtime_composition import build_openapi as _build_current_openapi
from .conversation_api import (
    CONVERSATION_COLLECTION,
    CONVERSATION_COLLECTIONS,
    CONVERSATION_COMMANDS,
    CONVERSATION_MESSAGE_COLLECTION,
    register_conversation_control_plane,
)
from .conversation_waiting import (
    CONVERSATION_RESUME_TASK_COMMAND,
    register_conversation_waiting_control_plane,
)
from .extensions import (
    CommandHandler,
    ResourceService,
    _reject_private_payload,
    _validate_command_name,
)
from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, RequestContext

_ALL_CONVERSATION_COMMANDS = (*CONVERSATION_COMMANDS, CONVERSATION_RESUME_TASK_COMMAND)


class ControlPlane(_CurrentControlPlane):
    """Current composed Control Plane plus optional task-centric conversations."""

    def __init__(
        self,
        *args: Any,
        conversation_service: ConversationService | None = None,
        conversation_agent_service: AgentService | None = None,
        conversation_file_provider: FileProvider | None = None,
        **kwargs: Any,
    ) -> None:
        if conversation_service is not None:
            supplied_resources = kwargs.get("resource_services")
            if isinstance(supplied_resources, Mapping):
                conflicts = sorted(set(supplied_resources).intersection(CONVERSATION_COLLECTIONS))
                if conflicts:
                    raise ValueError(
                        "resource_services conflict with canonical conversation routes: "
                        f"{conflicts!r}"
                    )
            supplied_commands = kwargs.get("command_handlers")
            if isinstance(supplied_commands, Mapping):
                conflicts = sorted(set(supplied_commands).intersection(_ALL_CONVERSATION_COMMANDS))
                if conflicts:
                    raise ValueError(
                        "command_handlers conflict with canonical conversation commands: "
                        f"{conflicts!r}"
                    )
        elif conversation_agent_service is not None or conversation_file_provider is not None:
            raise ValueError("conversation Agent/File dependencies require conversation_service")

        self._installing_conversations = False
        super().__init__(*args, **kwargs)
        self._conversation_service = conversation_service
        if conversation_service is not None:
            self._installing_conversations = True
            try:
                register_conversation_control_plane(
                    self,
                    conversation_service,
                    agent_service=conversation_agent_service,
                    file_provider=conversation_file_provider,
                )
                register_conversation_waiting_control_plane(self, conversation_service)
            finally:
                self._installing_conversations = False

    @property
    def conversation_service(self) -> ConversationService | None:
        return self._conversation_service

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if command not in _ALL_CONVERSATION_COMMANDS or self._conversation_service is None:
            return await super().execute_command(context, command, resource_ref, payload)
        _validate_command_name(command)
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
                details={"header": "Idempotency-Key"},
            )
        handler = self._command_handlers.get(command)
        if handler is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"canonical command is not registered: {command}",
                details={"command": command},
            )
        effective_payload = payload or {}
        result = await handler(context, resource_ref, effective_payload)
        result = await self._normalize_conversation_task_links(
            command,
            resource_ref,
            effective_payload,
            result,
        )
        _reject_private_payload(result)
        return result

    async def _normalize_conversation_task_links(
        self,
        command: str,
        resource_ref: str,
        payload: Mapping[str, JsonValue],
        result: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Keep every Task-associated conversation on one canonical linkage invariant."""

        service = self._conversation_service
        if service is None:
            return result

        if command == "conversation.create":
            target = payload.get("target")
            if isinstance(target, Mapping) and target.get("kind") == "task":
                task_id = target.get("id")
                conversation_id = result.get("id")
                if isinstance(task_id, str) and isinstance(conversation_id, str):
                    linked = await service.link_task(
                        conversation_id=conversation_id,
                        task_id=task_id,
                    )
                    normalized = linked.to_json()
                    normalized["type"] = "conversation"
                    return normalized

        if command == "conversation.message.attach-task":
            task_id = payload.get("task_id")
            if isinstance(task_id, str):
                message = await service.get_message(resource_ref)
                await service.link_task(
                    conversation_id=message.conversation_id,
                    task_id=task_id,
                    message_id=message.id,
                )
        return result

    async def resume_task_from_conversation_input(
        self,
        context: RequestContext,
        *,
        task_id: str,
        conversation_id: str,
        message_id: str,
        request_payload_digest: str,
    ) -> dict[str, JsonValue]:
        """Explicitly resume one waiting canonical Task from referenced chat input.

        The user's text remains exclusively in ConversationMessage. Task metadata stores
        only canonical provenance references, while the lifecycle transition is the
        kernel-owned ``task.resumed`` event.
        """

        validate_id(task_id, "task")
        validate_id(conversation_id, "conversation")
        validate_id(message_id, "message")
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
            )

        state = await self._kernel.get_task(task_id)
        await self._authorize_for_task(
            context,
            "task:resume",
            task_id,
            state,
            request_payload_digest=request_payload_digest,
        )
        resume_key = f"{context.idempotency_key}:conversation-resume:{message_id}"

        # Preserve kernel idempotency on retries. resume_task checks the command record
        # before validating the current state, so a replay after a successful resume
        # returns the existing result while a first call against a non-waiting Task
        # still fails without mutating Task metadata.
        if state.status is not TaskStatus.WAITING:
            await self._kernel.resume_task(
                idempotency_key=resume_key,
                task_id=task_id,
                actor_ref=context.actor.principal_ref,
                source="control-plane:conversation",
            )
            return await self.get_task(context, task_id)

        input_ref: dict[str, JsonValue] = {
            "conversation_id": conversation_id,
            "message_id": message_id,
        }
        if state.task.metadata.get("conversation_input") != input_ref:
            await self._kernel.update_task(
                idempotency_key=(f"{context.idempotency_key}:conversation-input:{message_id}"),
                task_id=task_id,
                metadata={"conversation_input": input_ref},
                actor_ref=context.actor.principal_ref,
                source="control-plane:conversation",
            )

        await self._kernel.resume_task(
            idempotency_key=resume_key,
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane:conversation",
        )
        return await self.get_task(context, task_id)

    async def create_task(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Persist conversation linkage already authorized as part of Task creation.

        The base Task creation authorization digest includes the complete northbound
        payload. The current kernel creation event predates Task metadata support at
        creation time, so conversation linkage is materialized immediately afterwards
        as one canonical ``task.updated`` event using a deterministic child idempotency
        key. This does not create a second lifecycle or bypass Task state.
        """

        resource = await super().create_task(context, payload)
        raw_metadata = payload.get("metadata")
        if not isinstance(raw_metadata, Mapping):
            return resource
        conversation_id = raw_metadata.get("conversation_id")
        message_id = raw_metadata.get("conversation_message_id")
        if not isinstance(conversation_id, str) or not isinstance(message_id, str):
            return resource
        validate_id(conversation_id, "conversation")
        validate_id(message_id, "message")
        task_id = resource.get("id")
        if not isinstance(task_id, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical task creation did not return a task id",
            )
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
            )
        state = await self._kernel.get_task(task_id)
        metadata = dict(raw_metadata)
        if all(state.task.metadata.get(key) == value for key, value in metadata.items()):
            return await self.get_task(context, task_id)
        await self._kernel.update_task(
            idempotency_key=f"{context.idempotency_key}:conversation-link",
            task_id=task_id,
            metadata=metadata,
            actor_ref=context.actor.principal_ref,
            source="control-plane:conversation",
        )
        return await self.get_task(context, task_id)

    async def get_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        resource = await super().get_task(context, task_id)
        state = await self._kernel.get_task(task_id)
        if state.task.metadata:
            resource["metadata"] = cast(JsonValue, dict(state.task.metadata))
        return resource

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if collection in CONVERSATION_COLLECTIONS and not self._installing_conversations:
            raise ValueError(
                f"extension collection conflicts with canonical conversation route: {collection}"
            )
        super().register_resource_service(collection, service)

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if command in _ALL_CONVERSATION_COMMANDS and not self._installing_conversations:
            raise ValueError(
                f"extension command conflicts with canonical conversation command: {command}"
            )
        super().register_command(command, handler)


class ControlPlaneHTTP(_CurrentControlPlaneHTTP):
    """Add ergonomic chat routes while retaining the canonical command/resource seams."""

    def __init__(self, control_plane: ControlPlane) -> None:
        super().__init__(control_plane)
        self._conversation_control_plane = control_plane

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        # Authentication tests and downstream adapters may intentionally supply a
        # compatible older Control Plane object. Absence of the optional conversation
        # service must therefore behave exactly like the current base HTTP surface.
        if getattr(self._conversation_control_plane, "conversation_service", None) is None:
            return await super().handle(request)

        rewritten, created = _rewrite_conversation_request(request)
        response = await super().handle(rewritten)
        if created and response.status == 200:
            response = HTTPResponse(status=201, body=response.body, headers=dict(response.headers))
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = _augment_conversation_openapi(
                cast(dict[str, Any], deepcopy(response.body))
            )
            return HTTPResponse(
                status=response.status,
                body=cast(dict[str, JsonValue], specification),
                headers=dict(response.headers),
            )
        return response


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
    include_conversations: bool = False,
) -> dict[str, Any]:
    """Build the static composed schema without speculating about optional chat.

    Runtime HTTP exposes conversation routes when ``ConversationService`` is configured.
    Callers that explicitly need the standalone conversation schema can opt in.
    """

    if not include_conversations:
        return _build_current_openapi(
            extension_collections=extension_collections,
            extension_commands=extension_commands,
        )
    collections = tuple(sorted(set((*extension_collections, *CONVERSATION_COLLECTIONS))))
    commands = tuple(sorted(set((*extension_commands, *_ALL_CONVERSATION_COMMANDS))))
    return _augment_conversation_openapi(
        _build_current_openapi(
            extension_collections=collections,
            extension_commands=commands,
        )
    )


def _rewrite_conversation_request(request: HTTPRequest) -> tuple[HTTPRequest, bool]:
    prefix = f"/api/{API_VERSION}"
    path = request.path.rstrip("/") or "/"
    if not path.startswith(prefix):
        return request, False
    relative = path[len(prefix) :] or "/"
    segments = [segment for segment in relative.split("/") if segment]

    command: str | None = None
    resource_ref: str | None = None
    created = False
    query = dict(request.query)

    if request.method == "POST" and segments == [CONVERSATION_COLLECTION]:
        command = "conversation.create"
        resource_ref = CONVERSATION_COLLECTION
        created = True
    elif len(segments) == 2 and segments[0] == CONVERSATION_COLLECTION and ":" in segments[1]:
        conversation_id, operation = segments[1].rsplit(":", 1)
        if request.method == "POST" and operation in {
            "archive",
            "reopen",
            "link-run",
            "link-artifact",
        }:
            command = f"conversation.{operation}"
            resource_ref = conversation_id
    elif (
        len(segments) == 3 and segments[0] == CONVERSATION_COLLECTION and segments[2] == "messages"
    ):
        conversation_id = segments[1]
        if request.method == "POST":
            command = "conversation.message.add"
            resource_ref = conversation_id
            created = True
        elif request.method == "GET":
            query["filter[conversation_id]"] = conversation_id
            return (
                HTTPRequest(
                    method="GET",
                    path=f"{prefix}/{CONVERSATION_MESSAGE_COLLECTION}",
                    headers=request.headers,
                    query=query,
                    body=request.body,
                    trusted_actor=request.trusted_actor,
                ),
                False,
            )
    elif (
        len(segments) == 2
        and segments[0] == CONVERSATION_MESSAGE_COLLECTION
        and ":" in segments[1]
        and request.method == "POST"
    ):
        message_id, operation = segments[1].rsplit(":", 1)
        if operation == "create-task":
            command = "conversation.message.create-task"
            resource_ref = message_id
            created = True
        elif operation == "attach-task":
            command = "conversation.message.attach-task"
            resource_ref = message_id
        elif operation == "resume-task":
            command = CONVERSATION_RESUME_TASK_COMMAND
            resource_ref = message_id

    if command is None or resource_ref is None:
        return request, False
    body = dict(request.body)
    body["resource_ref"] = resource_ref
    return (
        HTTPRequest(
            method="POST",
            path=f"{prefix}/commands/{command}",
            headers=request.headers,
            query=request.query,
            body=body,
            trusted_actor=request.trusted_actor,
        ),
        created,
    )


def _augment_conversation_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    paths = specification.get("paths")
    if not isinstance(paths, dict):
        return specification
    paths[f"/api/{API_VERSION}/conversations"] = {
        **cast(dict[str, Any], paths.get(f"/api/{API_VERSION}/conversations", {})),
        "post": _operation("createConversation", "Create canonical conversation", 201),
    }
    paths[f"/api/{API_VERSION}/conversations/{{conversation_id}}/messages"] = {
        "get": _operation("listConversationMessages", "List canonical conversation messages", 200),
        "post": _operation("addConversationMessage", "Append canonical conversation message", 201),
    }
    paths[f"/api/{API_VERSION}/conversations/{{conversation_id}}:archive"] = {
        "post": _operation("archiveConversation", "Archive canonical conversation", 200)
    }
    paths[f"/api/{API_VERSION}/conversations/{{conversation_id}}:reopen"] = {
        "post": _operation("reopenConversation", "Reopen canonical conversation", 200)
    }
    paths[f"/api/{API_VERSION}/conversations/{{conversation_id}}:link-run"] = {
        "post": _operation("linkConversationRun", "Link canonical Run", 200)
    }
    paths[f"/api/{API_VERSION}/conversations/{{conversation_id}}:link-artifact"] = {
        "post": _operation("linkConversationArtifact", "Link canonical Artifact", 200)
    }
    paths[f"/api/{API_VERSION}/conversation-messages/{{message_id}}:create-task"] = {
        "post": _operation("createTaskFromConversationMessage", "Create canonical Task", 201)
    }
    paths[f"/api/{API_VERSION}/conversation-messages/{{message_id}}:attach-task"] = {
        "post": _operation("attachConversationMessageToTask", "Attach message to Task", 200)
    }
    paths[f"/api/{API_VERSION}/conversation-messages/{{message_id}}:resume-task"] = {
        "post": _operation(
            "resumeTaskFromConversationMessage",
            "Explicitly provide referenced user input and resume a waiting canonical Task",
            200,
        )
    }
    specification["x-conversation-task-centric"] = True
    specification["x-conversation-provider-private-sessions-canonical"] = False
    return specification


def _operation(operation_id: str, description: str, status: int) -> dict[str, Any]:
    return {
        "operationId": operation_id,
        "description": description,
        "responses": {str(status): {"description": description}},
    }


__all__ = ["ControlPlane", "ControlPlaneASGI", "ControlPlaneHTTP", "build_openapi"]
