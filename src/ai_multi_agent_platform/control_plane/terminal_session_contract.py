"""Control Plane extension for canonical terminal/execution sessions (issue #73)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast
from urllib.parse import parse_qsl
from uuid import NAMESPACE_URL, uuid4, uuid5

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, OperationControl
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.terminal import (
    SessionContext,
    SessionCreateRequest,
    SessionMode,
    SessionType,
    TerminalDimensions,
    TerminalSession,
    TerminalSessionService,
)
from ai_multi_agent_platform.workspaces import WorkspaceProvider

from .extensions import CommandHandler, ResourceService
from .http import ControlPlaneASGI
from .models import (
    API_VERSION,
    ActorContext,
    OwnerType,
    PageQuery,
    RequestContext,
    api_exception_from_contract,
)

TERMINAL_WS_SUBPROTOCOL = "platform.terminal.v1"
_EXECUTION_OWNING_SESSION_TYPES = frozenset(
    {SessionType.AGENT, SessionType.WORKER, SessionType.PROCESS}
)
RunCanceller = Callable[[RequestContext, str, str, str], Awaitable[None]]


class TerminalSessionResourceService(ResourceService):
    """Read surface registered as the canonical ``terminal-sessions`` collection."""

    def __init__(self, sessions: TerminalSessionService) -> None:
        self._sessions = sessions

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        filters = query.filters or {}
        workspace_id = filters.get("workspace_id")
        project_id = filters.get("project_id")
        sessions = await self._sessions.list_sessions(
            actor_ref=context.actor.principal_ref,
            operation=_operation(context, project_id=project_id),
            workspace_id=workspace_id,
        )
        return tuple(item.to_json() for item in sessions)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        session = await self._sessions.get_session(
            resource_id,
            actor_ref=context.actor.principal_ref,
            operation=_operation(context),
        )
        return session.to_json()


def terminal_resource_services(
    sessions: TerminalSessionService,
) -> dict[str, ResourceService]:
    return {"terminal-sessions": TerminalSessionResourceService(sessions)}


def terminal_command_handlers(
    sessions: TerminalSessionService,
    *,
    kernel: PlatformKernel | None = None,
    workspace_provider: WorkspaceProvider | None = None,
    run_canceller: RunCanceller | None = None,
) -> dict[str, CommandHandler]:
    async def create(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        project_id = resource_ref
        workspace_id = _required_string(payload, "workspace_id")
        await _validate_workspace_project(workspace_provider, workspace_id, project_id)
        try:
            session_type = SessionType(_required_string(payload, "session_type"))
            mode = SessionMode(_required_string(payload, "mode"))
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                str(exc),
            ) from exc
        session_context = SessionContext(
            project_id=project_id,
            workspace_id=workspace_id,
            task_id=_optional_string(payload, "task_id"),
            run_id=_optional_string(payload, "run_id"),
            worker_id=_optional_string(payload, "worker_id"),
            node_id=_optional_string(payload, "node_id"),
        )
        await _validate_task_run_context(kernel, session_context)
        request = SessionCreateRequest(
            session_id=_create_session_id(context, project_id, payload),
            session_type=session_type,
            context=session_context,
            mode=mode,
            actor_ref=context.actor.principal_ref,
            operation=_operation(context, project_id=project_id),
            adapter_id=_optional_string(payload, "adapter_id") or "reference-terminal",
            dimensions=_dimensions(payload.get("dimensions")),
            encoding=_optional_string(payload, "encoding") or "utf-8",
            policy_classification=_string_tuple(payload.get("policy_classification")),
            inactivity_timeout_seconds=_optional_positive_int(
                payload,
                "inactivity_timeout_seconds",
            ),
            retain_transcript=_optional_bool(payload, "retain_transcript"),
        )
        session = await sessions.create_session(
            request,
            approval_id=_optional_string(payload, "approval_id"),
        )
        return session.to_json()

    async def input_command(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await sessions.send_input(
            resource_ref,
            _required_string(payload, "data", allow_empty=True),
            actor_ref=context.actor.principal_ref,
            operation=_operation(context),
            approval_id=_optional_string(payload, "approval_id"),
        )
        session = await sessions.get_session(
            resource_ref,
            actor_ref=context.actor.principal_ref,
            operation=_operation(context),
        )
        return session.to_json()

    async def terminate(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        session = await _terminate_session(
            sessions,
            run_canceller,
            resource_ref,
            context=context,
            reason=_optional_string(payload, "reason"),
            approval_id=_optional_string(payload, "approval_id"),
            idempotency_key=_required_idempotency_key(context),
        )
        return session.to_json()

    async def resize(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        dimensions = _dimensions(payload)
        if dimensions is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "resize requires columns and rows",
            )
        session = await sessions.resize(
            resource_ref,
            dimensions,
            actor_ref=context.actor.principal_ref,
            operation=_operation(context),
        )
        return session.to_json()

    return {
        "terminal.session.create": create,
        "terminal.session.input": input_command,
        "terminal.session.resize": resize,
        "terminal.session.terminate": terminate,
    }


class TerminalSessionASGI:
    """Add a canonical WebSocket stream while delegating HTTP/SSE to the base ASGI app."""

    def __init__(
        self,
        base: ControlPlaneASGI,
        sessions: TerminalSessionService,
        *,
        run_canceller: RunCanceller | None = None,
    ) -> None:
        self._base = base
        self._sessions = sessions
        self._run_canceller = run_canceller

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope.get("type") != "websocket":
            await self._base(scope, receive, send)
            return
        await self._websocket(scope, receive, send)

    async def _websocket(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        session_id = _stream_session_id(str(scope.get("path", "/")))
        if session_id is None:
            await send({"type": "websocket.close", "code": 4404, "reason": "route not found"})
            return
        subprotocols = scope.get("subprotocols", [])
        if TERMINAL_WS_SUBPROTOCOL not in subprotocols:
            await send(
                {
                    "type": "websocket.close",
                    "code": 4406,
                    "reason": "terminal subprotocol required",
                }
            )
            return

        headers = _decode_headers(scope.get("headers", []))
        query = dict(
            parse_qsl(
                bytes(scope.get("query_string", b"")).decode("utf-8"),
                keep_blank_values=True,
            )
        )
        request_id = headers.get("x-request-id") or f"request_{uuid4()}"
        correlation_id = headers.get("x-correlation-id") or request_id
        context = RequestContext(
            request_id=request_id,
            correlation_id=correlation_id,
            actor=_actor_context(headers),
            idempotency_key=headers.get("idempotency-key"),
        )
        try:
            after_sequence = int(query.get("after_sequence", "0"))
            if after_sequence < 0:
                raise ValueError
        except ValueError:
            await send(
                {
                    "type": "websocket.close",
                    "code": 4400,
                    "reason": "invalid after_sequence",
                }
            )
            return

        connect = await receive()
        if connect.get("type") != "websocket.connect":
            await send({"type": "websocket.close", "code": 4400, "reason": "connect required"})
            return

        operation = _operation(context)
        try:
            attachment = await self._sessions.attach(
                session_id,
                actor_ref=context.actor.principal_ref,
                operation=operation,
                after_sequence=after_sequence,
            )
            session = await self._sessions.get_session(
                session_id,
                actor_ref=context.actor.principal_ref,
                operation=operation,
            )
        except ContractError as exc:
            error = api_exception_from_contract(exc)
            close_code = (
                4401
                if error.status == 401
                else 4403
                if error.status == 403
                else 4404
                if error.status == 404
                else 4400
            )
            await send(
                {
                    "type": "websocket.close",
                    "code": close_code,
                    "reason": error.code,
                }
            )
            return

        await send({"type": "websocket.accept", "subprotocol": TERMINAL_WS_SUBPROTOCOL})
        await _send_json(
            send,
            {
                "type": "session.snapshot",
                "request_id": request_id,
                "correlation_id": correlation_id,
                "session": session.to_json(),
                "attachment": attachment.to_json(),
            },
        )

        stream_task = asyncio.create_task(
            self._pump_stream(
                session_id,
                context,
                after_sequence=after_sequence,
                send=send,
            )
        )
        client_task = asyncio.create_task(
            self._consume_client(session_id, context, receive=receive, send=send)
        )
        try:
            done, pending = await asyncio.wait(
                {stream_task, client_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                await task
        finally:
            for task in (stream_task, client_task):
                if not task.done():
                    task.cancel()
            try:
                await self._sessions.detach(
                    attachment.id,
                    actor_ref=context.actor.principal_ref,
                    operation=operation,
                )
            except ContractError:
                pass

    async def _pump_stream(
        self,
        session_id: str,
        context: RequestContext,
        *,
        after_sequence: int,
        send: Any,
    ) -> None:
        async for frame in self._sessions.stream_frames(
            session_id,
            actor_ref=context.actor.principal_ref,
            operation=_operation(context),
            after_sequence=after_sequence,
        ):
            await _send_json(send, {"type": "stream.frame", "frame": frame.to_json()})
        session = await self._sessions.reconcile_session(session_id)
        await _send_json(send, {"type": "session.status", "session": session.to_json()})

    async def _consume_client(
        self,
        session_id: str,
        context: RequestContext,
        *,
        receive: Any,
        send: Any,
    ) -> None:
        operation = _operation(context)
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                return
            if message_type != "websocket.receive":
                continue
            raw = message.get("text")
            if not isinstance(raw, str):
                await _send_json(send, {"type": "error", "code": "text_frames_required"})
                continue
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("terminal message must be a JSON object")
                command = payload.get("type")
                if command == "input":
                    data = payload.get("data")
                    if not isinstance(data, str):
                        raise ValueError("input data must be a string")
                    await self._sessions.send_input(
                        session_id,
                        data,
                        actor_ref=context.actor.principal_ref,
                        operation=operation,
                        approval_id=_json_optional_string(payload, "approval_id"),
                    )
                elif command == "resize":
                    dimensions = _dimensions(payload)
                    if dimensions is None:
                        raise ValueError("resize requires columns and rows")
                    await self._sessions.resize(
                        session_id,
                        dimensions,
                        actor_ref=context.actor.principal_ref,
                        operation=operation,
                    )
                elif command == "terminate":
                    await _terminate_session(
                        self._sessions,
                        self._run_canceller,
                        session_id,
                        context=context,
                        reason=_json_optional_string(payload, "reason"),
                        approval_id=_json_optional_string(payload, "approval_id"),
                        idempotency_key=(
                            _json_optional_string(payload, "idempotency_key")
                            or context.idempotency_key
                            or f"{context.request_id}:terminal-terminate"
                        ),
                    )
                elif command == "detach":
                    return
                elif command == "ping":
                    await _send_json(send, {"type": "pong"})
                else:
                    raise ValueError("unknown terminal stream message type")
            except ContractError as exc:
                error = api_exception_from_contract(exc)
                await _send_json(
                    send,
                    {
                        "type": "error",
                        "code": error.code,
                        "message": error.message,
                        "details": error.details,
                    },
                )
            except (ValueError, json.JSONDecodeError) as exc:
                await _send_json(
                    send,
                    {"type": "error", "code": "invalid_request", "message": str(exc)},
                )


async def _terminate_session(
    sessions: TerminalSessionService,
    run_canceller: RunCanceller | None,
    session_id: str,
    *,
    context: RequestContext,
    reason: str | None,
    approval_id: str | None,
    idempotency_key: str,
) -> TerminalSession:
    operation = _operation(context)
    session = await sessions.terminate(
        session_id,
        actor_ref=context.actor.principal_ref,
        operation=operation,
        reason=reason,
        approval_id=approval_id,
    )
    if (
        session.session_type in _EXECUTION_OWNING_SESSION_TYPES
        and session.context.run_id is not None
        and session.context.task_id is not None
    ):
        if run_canceller is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "run-linked terminal termination requires canonical Control Plane integration",
            )
        await run_canceller(
            context,
            session.context.task_id,
            session.context.run_id,
            idempotency_key,
        )
    return session


async def _validate_workspace_project(
    workspace_provider: WorkspaceProvider | None,
    workspace_id: str,
    project_id: str,
) -> None:
    if workspace_provider is None:
        return
    workspace = await workspace_provider.get_workspace(workspace_id)
    if workspace.project_id != project_id:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "terminal workspace does not belong to the requested project",
            details={"workspace_id": workspace_id, "project_id": project_id},
        )


async def _validate_task_run_context(
    kernel: PlatformKernel | None,
    session_context: SessionContext,
) -> None:
    if kernel is None or session_context.task_id is None:
        return
    task = await kernel.get_task(session_context.task_id)
    if task.task.project_id != session_context.project_id:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "terminal task does not belong to the requested project",
            details={
                "task_id": session_context.task_id,
                "project_id": session_context.project_id,
            },
        )
    if session_context.run_id is not None:
        await kernel.get_run(session_context.task_id, session_context.run_id)


def _create_session_id(
    context: RequestContext,
    project_id: str,
    payload: Mapping[str, JsonValue],
) -> str:
    explicit = _optional_string(payload, "session_id")
    if explicit is not None:
        return explicit
    key = _required_idempotency_key(context)
    stable = uuid5(
        NAMESPACE_URL,
        f"ai-multi-agent-platform:terminal:create:{context.actor.principal_ref}:{project_id}:{key}",
    )
    return f"terminal_session_{stable}"


def _required_idempotency_key(context: RequestContext) -> str:
    key = context.idempotency_key
    if key is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Idempotency-Key is required for terminal mutation",
            details={"header": "Idempotency-Key"},
        )
    return key


def _operation(context: RequestContext, *, project_id: str | None = None) -> OperationContext:
    return OperationContext(
        correlation_id=context.correlation_id,
        owner_type=context.actor.owner_type,
        owner_id=context.actor.owner_id,
        project_id=project_id,
        control=OperationControl(idempotency_key=context.idempotency_key),
    )


def _actor_context(headers: Mapping[str, str]) -> ActorContext:
    raw_owner_type = headers.get("x-owner-type")
    owner_id = headers.get("x-owner-id")
    if raw_owner_type not in {None, "user", "organization", "team", "service"}:
        raw_owner_type = None
        owner_id = None
    if (raw_owner_type is None) != (owner_id is None):
        raw_owner_type = None
        owner_id = None
    return ActorContext(
        principal_ref=headers.get("x-principal-ref") or "local:anonymous",
        owner_type=cast(OwnerType | None, raw_owner_type),
        owner_id=owner_id,
    )


def _stream_session_id(path: str) -> str | None:
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) != 5:
        return None
    if segments[:3] != ["api", API_VERSION, "terminal-sessions"] or segments[4] != "stream":
        return None
    return segments[3]


def _decode_headers(raw_headers: Any) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in raw_headers:
        decoded[bytes(key).decode("latin-1").lower()] = bytes(value).decode("latin-1")
    return decoded


async def _send_json(send: Any, payload: dict[str, JsonValue]) -> None:
    await send(
        {
            "type": "websocket.send",
            "text": json.dumps(payload, separators=(",", ":"), default=str),
        }
    )


def _required_string(
    payload: Mapping[str, JsonValue],
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a string" + ("" if allow_empty else " and not blank"),
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
            f"{name} must be a non-blank string when provided",
            details={"field": name},
        )
    return value


def _optional_positive_int(payload: Mapping[str, JsonValue], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a positive integer when provided",
            details={"field": name},
        )
    return value


def _optional_bool(
    payload: Mapping[str, JsonValue],
    name: str,
    *,
    default: bool = False,
) -> bool:
    value = payload.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a boolean when provided",
            details={"field": name},
        )
    return value


def _json_optional_string(payload: Mapping[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string when provided")
    return value


def _string_tuple(value: JsonValue) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "policy_classification must be a list of non-blank strings",
            details={"field": "policy_classification"},
        )
    classifications: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "policy_classification must be a list of non-blank strings",
                details={"field": "policy_classification"},
            )
        classifications.append(item)
    return tuple(classifications)


def _dimensions(value: JsonValue | Mapping[str, Any]) -> TerminalDimensions | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_REQUEST, "dimensions must be an object")
    columns = value.get("columns")
    rows = value.get("rows")
    if not isinstance(columns, int) or isinstance(columns, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, "columns must be an integer")
    if not isinstance(rows, int) or isinstance(rows, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, "rows must be an integer")
    try:
        return TerminalDimensions(columns=columns, rows=rows)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
