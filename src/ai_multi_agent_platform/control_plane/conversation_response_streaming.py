"""Provider-neutral assistant/agent response streaming for canonical Conversations (#72)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import (
    Conversation,
    ConversationContentBlock,
    ConversationMessage,
    ConversationService,
    ConversationStatus,
    MessageRole,
)
from ai_multi_agent_platform.conversations.responses import (
    ConversationResponseChunkKind,
    ConversationResponseProvider,
    ConversationResponseRequest,
    ConversationResponseTarget,
)
from ai_multi_agent_platform.domain import validate_id

from .http import (
    ASGIReceive,
    ASGISend,
    HTTPRequest,
    HTTPResponse,
    _request_context,
    _send_response,
    _send_sse_error,
)
from .models import API_VERSION, APIException, RequestContext, api_exception_from_contract

CONVERSATION_RESPONSE_STREAM_SUFFIX = ("response", "stream")


class ConversationResponseControlPlane(Protocol):
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


async def stream_conversation_response(
    control_plane: ConversationResponseControlPlane,
    service: ConversationService,
    provider: ConversationResponseProvider,
    context: RequestContext,
    message_id: str,
) -> AsyncIterator[dict[str, JsonValue]]:
    """Stream tentative response text and commit one durable Assistant message on success."""

    validate_id(message_id, "message")
    if context.idempotency_key is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Idempotency-Key is required for conversational response streaming",
            details={"header": "Idempotency-Key"},
        )
    source = await service.get_message(message_id)
    conversation = await service.get_conversation(source.conversation_id)
    if conversation.status is not ConversationStatus.OPEN:
        raise ContractError(ErrorCode.INVALID_REQUEST, "responses require an open conversation")
    if source.role is not MessageRole.USER:
        raise ContractError(ErrorCode.INVALID_REQUEST, "responses can only target user messages")
    if conversation.project_id is None and conversation.owner_ref != context.actor.principal_ref:
        raise ContractError(ErrorCode.FORBIDDEN, "private conversation belongs to another actor")
    await control_plane._authorize(
        context,
        "conversation-message:create",
        conversation.id,
        project_id=conversation.project_id,
    )

    history = await _conversation_history(service, conversation.id)
    replay = _find_committed_response(history, source.id, context.idempotency_key)
    if replay is not None:
        return _single_event(_committed_event(conversation, source, replay, replayed=True))
    if not history or history[-1].id != source.id:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "response source must be the latest durable conversation message",
        )

    request = ConversationResponseRequest(
        request_id=context.request_id,
        correlation_id=context.correlation_id,
        actor_ref=context.actor.principal_ref,
        conversation_id=conversation.id,
        source_message_id=source.id,
        target=_response_target(conversation),
        history=history,
        model_preference=conversation.model_preference,
    )

    async def iterator() -> AsyncIterator[dict[str, JsonValue]]:
        text_parts: list[str] = []
        model_config_id: str | None = None
        async for chunk in provider.stream_response(request):
            if chunk.model_config_id is not None:
                model_config_id = chunk.model_config_id
            if chunk.kind is ConversationResponseChunkKind.TEXT:
                text_parts.append(chunk.text)
                yield {
                    "id": f"response_event_{uuid4()}",
                    "type": "conversation.response.delta",
                    "conversation_id": conversation.id,
                    "source_message_id": source.id,
                    "authoritative": False,
                    "tentative": True,
                    "delta": {"kind": "text", "text": chunk.text},
                    "model_config_id": model_config_id,
                }
            else:
                yield {
                    "id": f"response_event_{uuid4()}",
                    "type": "conversation.response.activity",
                    "conversation_id": conversation.id,
                    "source_message_id": source.id,
                    "authoritative": False,
                    "tentative": True,
                    "summary": chunk.text,
                    "model_config_id": model_config_id,
                }

        text = "".join(text_parts)
        if not text:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "conversation response provider completed without Assistant text",
            )
        target = request.target
        committed = await service.append_message(
            conversation_id=conversation.id,
            sender_ref=_canonical_response_sender(target),
            role=MessageRole.ASSISTANT,
            content=(ConversationContentBlock.text_block(text),),
            model_config_id=model_config_id,
            correlation_id=context.correlation_id,
            causation_id=context.idempotency_key,
            metadata={
                "response_to": source.id,
                "target": target.to_json(),
                "streamed": True,
            },
        )
        yield _committed_event(conversation, source, committed, replayed=False)

    return iterator()


async def _conversation_history(
    service: ConversationService,
    conversation_id: str,
) -> tuple[ConversationMessage, ...]:
    messages: list[ConversationMessage] = []
    cursor: str | None = None
    while True:
        page, cursor = await service.list_messages(conversation_id, limit=200, cursor=cursor)
        messages.extend(page)
        if cursor is None:
            return tuple(messages)


def _find_committed_response(
    history: tuple[ConversationMessage, ...],
    source_message_id: str,
    idempotency_key: str,
) -> ConversationMessage | None:
    for message in reversed(history):
        if (
            message.role is MessageRole.ASSISTANT
            and message.causation_id == idempotency_key
            and message.metadata.get("response_to") == source_message_id
        ):
            return message
    return None


def _response_target(conversation: Conversation) -> ConversationResponseTarget:
    raw = conversation.metadata.get("target")
    if isinstance(raw, Mapping):
        kind = raw.get("kind")
        target_id = raw.get("id")
        revision = raw.get("revision")
        if isinstance(kind, str) and isinstance(target_id, str):
            if revision is not None and (
                not isinstance(revision, int) or isinstance(revision, bool)
            ):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "conversation target revision is not canonical",
                )
            return ConversationResponseTarget(kind=kind, id=target_id, revision=revision)
    if conversation.default_agent is not None:
        default = conversation.default_agent
        return ConversationResponseTarget(
            kind=default.kind.value,
            id=default.id,
            revision=default.revision,
        )
    if conversation.project_id is not None:
        return ConversationResponseTarget(kind="project", id=conversation.project_id)
    return ConversationResponseTarget(kind="orchestrator", id="platform")


def _canonical_response_sender(target: ConversationResponseTarget) -> str:
    if target.kind in {"agent", "agent_team"}:
        return f"{target.kind}:{target.id}"
    return "orchestrator:platform"


def _message_resource(message: ConversationMessage) -> dict[str, JsonValue]:
    return {**message.to_json(), "type": "conversation-message"}


def _committed_event(
    conversation: Conversation,
    source: ConversationMessage,
    message: ConversationMessage,
    *,
    replayed: bool,
) -> dict[str, JsonValue]:
    return {
        "id": f"response_event_{uuid4()}",
        "type": "conversation.response.committed",
        "conversation_id": conversation.id,
        "source_message_id": source.id,
        "authoritative": False,
        "tentative": False,
        "durable": True,
        "replayed": replayed,
        "message": _message_resource(message),
    }


async def _single_event(event: dict[str, JsonValue]) -> AsyncIterator[dict[str, JsonValue]]:
    yield event


class ConversationResponseASGI:
    """POST-SSE transport for one explicit conversational response operation."""

    def __init__(self, inner: Any, http: Any) -> None:
        self._inner = inner
        self._http = http
        self._control_plane = getattr(http, "_control_plane", None)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))
        if scope.get("type") != "http" or method != "POST" or not is_response_stream_path(path):
            await self._inner(scope, receive, send)
            return
        await self._stream(path, scope, send)

    async def _stream(self, path: str, scope: dict[str, Any], send: ASGISend) -> None:
        headers = _decode_headers(scope.get("headers", []))
        request_id = headers.get("x-request-id") or f"request_{uuid4()}"
        correlation_id = headers.get("x-correlation-id") or request_id
        started = False
        try:
            prepared = self._http.prepare_stream_request(
                HTTPRequest(method="POST", path=path, headers=headers),
                request_id=request_id,
                correlation_id=correlation_id,
            )
            if isinstance(prepared, HTTPResponse):
                await _send_response(prepared, send)
                return
            context = _request_context(prepared, request_id, correlation_id)
            control_plane = self._control_plane
            service = getattr(control_plane, "conversation_service", None)
            provider = getattr(control_plane, "conversation_response_provider", None)
            if control_plane is None or not isinstance(service, ConversationService):
                raise ContractError(ErrorCode.NOT_FOUND, "conversation service is not configured")
            if provider is None:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "conversation response provider is not configured",
                )
            message_id = _message_id_from_response_stream_path(path)
            stream = await stream_conversation_response(
                cast(ConversationResponseControlPlane, control_plane),
                service,
                cast(ConversationResponseProvider, provider),
                context,
                message_id,
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/event-stream"),
                        (b"cache-control", b"no-cache"),
                        (b"x-api-version", API_VERSION.encode("ascii")),
                        (b"x-request-id", request_id.encode()),
                        (b"x-correlation-id", correlation_id.encode()),
                    ],
                }
            )
            started = True
            async for event in stream:
                event_type = event.get("type")
                if not isinstance(event_type, str):
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "conversation response event has no event type",
                    )
                payload = json.dumps(event, separators=(",", ":"), default=str)
                frame = f"event: {event_type}\ndata: {payload}\n\n"
                await send(
                    {
                        "type": "http.response.body",
                        "body": frame.encode("utf-8"),
                        "more_body": True,
                    }
                )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except ContractError as exc:
            error = api_exception_from_contract(exc)
            if started:
                await _send_sse_error(error, request_id, correlation_id, send)
            else:
                await _send_response(
                    self._http._error_response(error, request_id, correlation_id),
                    send,
                )
        except (APIException, ValueError, TypeError) as exc:
            error = (
                exc
                if isinstance(exc, APIException)
                else APIException(status=400, code="invalid_request", message=str(exc))
            )
            if started:
                await _send_sse_error(error, request_id, correlation_id, send)
            else:
                await _send_response(
                    self._http._error_response(error, request_id, correlation_id),
                    send,
                )


def is_response_stream_path(path: str) -> bool:
    segments = [segment for segment in path.split("/") if segment]
    return (
        len(segments) == 6
        and segments[:3] == ["api", API_VERSION, "conversation-messages"]
        and tuple(segments[4:]) == CONVERSATION_RESPONSE_STREAM_SUFFIX
    )


def _message_id_from_response_stream_path(path: str) -> str:
    if not is_response_stream_path(path):
        raise APIException(status=404, code="not_found", message="route not found")
    return [segment for segment in path.split("/") if segment][3]


def augment_response_stream_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    paths = specification.get("paths")
    if not isinstance(paths, dict):
        return specification
    paths[f"/api/{API_VERSION}/conversation-messages/{{message_id}}/response/stream"] = {
        "post": {
            "operationId": "streamConversationResponse",
            "description": (
                "Stream tentative provider-neutral Assistant/Agent text and commit one durable "
                "Conversation Message when the response completes."
            ),
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1},
                }
            ],
            "responses": {
                "200": {"description": "Server-Sent Events; deltas are non-authoritative"},
                "401": {"description": "Authentication required"},
                "403": {"description": "Conversation access denied"},
                "503": {"description": "No conversational response provider configured"},
            },
        }
    }
    specification["x-conversation-response-provider-neutral"] = True
    specification["x-conversation-response-deltas-authoritative"] = False
    return specification


def _decode_headers(raw_headers: Any) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in raw_headers:
        decoded[bytes(key).decode("latin-1").lower()] = bytes(value).decode("latin-1")
    return decoded


__all__ = [
    "CONVERSATION_RESPONSE_STREAM_SUFFIX",
    "ConversationResponseASGI",
    "augment_response_stream_openapi",
    "is_response_stream_path",
    "stream_conversation_response",
]
