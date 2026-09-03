"""Canonical Control Plane composition for terminal sessions (#73)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, cast
from urllib.parse import parse_qsl

from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationRequest,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, OperationControl
from ai_multi_agent_platform.security.authorization import infer_actor_identity
from ai_multi_agent_platform.terminal import (
    SessionAttachment,
    SessionContext,
    TerminalDimensions,
    TerminalFrame,
    TerminalSession,
    TerminalSessionService,
)
from ai_multi_agent_platform.workspaces import WorkspaceProvider

from .authenticated_authorization import ControlPlane as _BaseControlPlane
from .automation_api import ControlPlaneHTTP as _AutomationControlPlaneHTTP
from .automation_api import build_openapi
from .extensions import (
    _reject_private_payload,
    _validate_command_name,
    _validate_resources,
)
from .http import ControlPlaneASGI as _BaseControlPlaneASGI
from .http import HTTPRequest, HTTPResponse, _request_context
from .models import API_VERSION, APIException, PageQuery, RequestContext, paginate
from .service import _payload_digest
from .terminal_session_contract import (
    TerminalSessionASGI,
    terminal_command_handlers,
    terminal_resource_services,
)

TERMINAL_COLLECTION = "terminal-sessions"
TERMINAL_COMMANDS = (
    "terminal.session.create",
    "terminal.session.input",
    "terminal.session.resize",
    "terminal.session.terminate",
)

_TERMINAL_STREAM_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "terminal_stream_request_context",
    default=None,
)


class ControlPlane(_BaseControlPlane):
    """Current platform Control Plane with optional first-class terminal composition."""

    def __init__(
        self,
        *args: Any,
        terminal_sessions: TerminalSessionService | None = None,
        **kwargs: Any,
    ) -> None:
        workspace_provider = cast(WorkspaceProvider | None, kwargs.get("workspace_provider"))
        if terminal_sessions is not None:
            supplied_resources = kwargs.get("resource_services")
            if (
                isinstance(supplied_resources, Mapping)
                and TERMINAL_COLLECTION in supplied_resources
            ):
                raise ValueError(
                    "resource_services conflict with canonical terminal route: "
                    f"{TERMINAL_COLLECTION}"
                )
            supplied_commands = kwargs.get("command_handlers")
            if isinstance(supplied_commands, Mapping):
                conflicts = sorted(set(supplied_commands).intersection(TERMINAL_COMMANDS))
                if conflicts:
                    raise ValueError(
                        f"command_handlers conflict with canonical terminal commands: {conflicts!r}"
                    )

        super().__init__(*args, **kwargs)
        self._terminal_sessions = terminal_sessions
        if terminal_sessions is None:
            return

        for collection, service in terminal_resource_services(terminal_sessions).items():
            self.register_resource_service(collection, service)
        handlers = terminal_command_handlers(
            terminal_sessions,
            kernel=self._kernel,
            workspace_provider=workspace_provider,
            run_canceller=self._cancel_run_from_terminal,
        )
        for command, handler in handlers.items():
            self.register_command(command, handler)

    @property
    def terminal_sessions(self) -> TerminalSessionService | None:
        return self._terminal_sessions

    async def list_extension_resources(
        self,
        context: RequestContext,
        collection: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        if collection != TERMINAL_COLLECTION or self._terminal_sessions is None:
            return await super().list_extension_resources(context, collection, query)

        filters = query.filters or {}
        project_id = filters.get("project_id")
        workspace_id = filters.get("workspace_id")
        await self._authorize_terminal(
            context,
            "terminal-session:list",
            collection,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        service = self._registered_resource_service(collection)
        resources = list(await service.list_resources(context, query))
        _validate_resources(collection, resources)
        return paginate(resources, query)

    async def get_extension_resource(
        self,
        context: RequestContext,
        collection: str,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        if collection != TERMINAL_COLLECTION or self._terminal_sessions is None:
            return await super().get_extension_resource(context, collection, resource_id)

        scope = self._terminal_scope(resource_id)
        await self._authorize_terminal(
            context,
            "terminal-session:read",
            resource_id,
            project_id=scope.project_id,
            workspace_id=scope.workspace_id,
        )
        service = self._registered_resource_service(collection)
        resource = await service.get_resource(context, resource_id)
        _validate_resources(collection, [resource])
        return resource

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if command not in TERMINAL_COMMANDS or self._terminal_sessions is None:
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
        if command == "terminal.session.create":
            project_id = resource_ref
            workspace_id = _required_workspace_id(effective_payload)
        else:
            scope = self._terminal_scope(resource_ref)
            project_id = scope.project_id
            workspace_id = scope.workspace_id

        await self._authorize_terminal(
            context,
            command,
            resource_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            request_payload_digest=_payload_digest(effective_payload),
        )
        result = await handler(context, resource_ref, effective_payload)
        _reject_private_payload(result)
        return result

    def _terminal_scope(self, session_id: str) -> SessionContext:
        if self._terminal_sessions is None:
            raise ContractError(ErrorCode.NOT_FOUND, "terminal session service is not configured")
        return self._terminal_sessions._session(session_id).context

    async def _authorize_terminal(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        project_id: str | None,
        workspace_id: str | None,
        request_payload_digest: str | None = None,
    ) -> None:
        """Authorize Terminal with its canonical project/workspace and #36 trust context."""

        if self._authorization is None:
            return
        actor_type = context.actor.actor_type
        if actor_type is None:
            actor_type = infer_actor_identity(context.actor.principal_ref).actor_type.value
        decision = await self._authorization.authorize(
            AuthorizationRequest(
                principal_ref=context.actor.principal_ref,
                actor_type=actor_type,
                action=action,
                resource_ref=resource_ref,
                context=OperationContext(
                    correlation_id=context.correlation_id,
                    owner_type=context.actor.owner_type,
                    owner_id=context.actor.owner_id,
                    project_id=project_id,
                    control=OperationControl(idempotency_key=context.idempotency_key),
                ),
                workspace_id=workspace_id,
                trust_context=context.actor.trust_context,
                request_payload_digest=request_payload_digest,
            )
        )
        canonical = normalize_authorization_decision(decision)
        if canonical.allowed:
            return
        details: dict[str, JsonValue] = {
            "authorization_outcome": canonical.outcome.value,
        }
        if canonical.policy_id is not None:
            details["policy_id"] = canonical.policy_id
        details.update(dict(canonical.constraints))
        raise ContractError(
            ErrorCode.FORBIDDEN,
            canonical.reason or "operation is forbidden",
            details=details,
        )

    async def _cancel_run_from_terminal(
        self,
        context: RequestContext,
        task_id: str,
        run_id: str,
        idempotency_key: str,
    ) -> None:
        """Cancel run ownership through the normal authorized Control Plane boundary."""

        trusted_context = _TERMINAL_STREAM_CONTEXT.get() or context
        await self.cancel_run(
            replace(trusted_context, idempotency_key=idempotency_key),
            task_id,
            run_id,
        )


ControlPlaneHTTP = _AutomationControlPlaneHTTP


class _AuthorizedStreamSessions:
    """Apply canonical Control Plane authorization before WebSocket session operations."""

    def __init__(self, control_plane: ControlPlane, inner: TerminalSessionService) -> None:
        self._control_plane = control_plane
        self._inner = inner

    async def _authorize(self, action: str, resource_ref: str) -> RequestContext:
        context = _TERMINAL_STREAM_CONTEXT.get()
        if context is None:
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "terminal stream is missing trusted request context",
            )
        scope = self._control_plane._terminal_scope(resource_ref)
        await self._control_plane._authorize_terminal(
            context,
            action,
            resource_ref,
            project_id=scope.project_id,
            workspace_id=scope.workspace_id,
        )
        return context

    async def attach(
        self,
        session_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
        after_sequence: int = 0,
    ) -> SessionAttachment:
        await self._authorize("terminal-session:read", session_id)
        return await self._inner.attach(
            session_id,
            actor_ref=actor_ref,
            operation=operation,
            after_sequence=after_sequence,
        )

    async def detach(
        self,
        attachment_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
    ) -> SessionAttachment:
        return await self._inner.detach(
            attachment_id,
            actor_ref=actor_ref,
            operation=operation,
        )

    async def get_session(
        self,
        session_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
    ) -> TerminalSession:
        await self._authorize("terminal-session:read", session_id)
        return await self._inner.get_session(
            session_id,
            actor_ref=actor_ref,
            operation=operation,
        )

    async def stream_frames(
        self,
        session_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
        after_sequence: int = 0,
    ) -> AsyncIterator[TerminalFrame]:
        await self._authorize("terminal-session:read", session_id)
        async for frame in self._inner.stream_frames(
            session_id,
            actor_ref=actor_ref,
            operation=operation,
            after_sequence=after_sequence,
        ):
            yield frame

    async def send_input(
        self,
        session_id: str,
        data: str,
        *,
        actor_ref: str,
        operation: OperationContext,
        approval_id: str | None = None,
    ) -> None:
        await self._authorize("terminal.session.input", session_id)
        await self._inner.send_input(
            session_id,
            data,
            actor_ref=actor_ref,
            operation=operation,
            approval_id=approval_id,
        )

    async def resize(
        self,
        session_id: str,
        dimensions: TerminalDimensions,
        *,
        actor_ref: str,
        operation: OperationContext,
    ) -> TerminalSession:
        await self._authorize("terminal.session.resize", session_id)
        return await self._inner.resize(
            session_id,
            dimensions,
            actor_ref=actor_ref,
            operation=operation,
        )

    async def terminate(
        self,
        session_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
        reason: str | None = None,
        approval_id: str | None = None,
    ) -> TerminalSession:
        await self._authorize("terminal.session.terminate", session_id)
        return await self._inner.terminate(
            session_id,
            actor_ref=actor_ref,
            operation=operation,
            reason=reason,
            approval_id=approval_id,
        )

    async def reconcile_session(self, session_id: str) -> TerminalSession:
        return await self._inner.reconcile_session(session_id)


class ControlPlaneASGI:
    """Standard ASGI composition including terminal WebSocket routing when configured."""

    def __init__(self, http: Any) -> None:
        base = _BaseControlPlaneASGI(http)
        control_plane = getattr(http, "_control_plane", None)
        if isinstance(control_plane, ControlPlane) and control_plane.terminal_sessions is not None:
            stream_sessions = cast(
                TerminalSessionService,
                _AuthorizedStreamSessions(control_plane, control_plane.terminal_sessions),
            )
            terminal = TerminalSessionASGI(
                base,
                stream_sessions,
                run_canceller=control_plane._cancel_run_from_terminal,
            )
            self._app: Any = _PreparedTerminalStreamASGI(terminal, http)
        else:
            self._app = base

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        await self._app(scope, receive, send)


class _PreparedTerminalStreamASGI:
    """Apply the HTTP stream identity boundary before the terminal WebSocket gateway."""

    def __init__(self, inner: TerminalSessionASGI, http: Any) -> None:
        self._inner = inner
        self._http = http

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path", "/"))
        if scope.get("type") != "websocket" or not _is_terminal_stream_path(path):
            await self._inner(scope, receive, send)
            return

        headers = _decode_headers(scope.get("headers", []))
        query = dict(
            parse_qsl(
                bytes(scope.get("query_string", b"")).decode("utf-8"),
                keep_blank_values=True,
            )
        )
        request_id = headers.get("x-request-id") or "terminal-websocket"
        correlation_id = headers.get("x-correlation-id") or request_id
        prepared = self._http.prepare_stream_request(
            HTTPRequest(method="GET", path=path, headers=headers, query=query),
            request_id=request_id,
            correlation_id=correlation_id,
        )
        if isinstance(prepared, HTTPResponse):
            await send(
                {
                    "type": "websocket.close",
                    "code": _websocket_close_code(prepared.status),
                    "reason": _response_error_code(prepared),
                }
            )
            return

        try:
            context = _request_context(prepared, request_id, correlation_id)
        except APIException as exc:
            await send(
                {
                    "type": "websocket.close",
                    "code": _websocket_close_code(exc.status),
                    "reason": exc.code,
                }
            )
            return
        except ValueError:
            await send(
                {
                    "type": "websocket.close",
                    "code": 4400,
                    "reason": "invalid_request",
                }
            )
            return

        trusted_headers = dict(prepared.headers)
        trusted_headers["x-principal-ref"] = context.actor.principal_ref
        if context.actor.owner_type is not None and context.actor.owner_id is not None:
            trusted_headers["x-owner-type"] = context.actor.owner_type
            trusted_headers["x-owner-id"] = context.actor.owner_id
        else:
            trusted_headers.pop("x-owner-type", None)
            trusted_headers.pop("x-owner-id", None)

        trusted_scope = dict(scope)
        trusted_scope["headers"] = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in trusted_headers.items()
        ]
        token = _TERMINAL_STREAM_CONTEXT.set(context)
        try:
            await self._inner(trusted_scope, receive, send)
        finally:
            _TERMINAL_STREAM_CONTEXT.reset(token)


def _required_workspace_id(payload: dict[str, JsonValue]) -> str:
    value = payload.get("workspace_id")
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "workspace_id must be a non-blank string",
            details={"field": "workspace_id"},
        )
    return value


def _is_terminal_stream_path(path: str) -> bool:
    segments = [segment for segment in path.split("/") if segment]
    return (
        len(segments) == 5
        and segments[:3] == ["api", API_VERSION, TERMINAL_COLLECTION]
        and segments[4] == "stream"
    )


def _decode_headers(raw_headers: Any) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in raw_headers:
        decoded[bytes(key).decode("latin-1").lower()] = bytes(value).decode("latin-1")
    return decoded


def _websocket_close_code(status: int) -> int:
    if status == 401:
        return 4401
    if status == 403:
        return 4403
    if status == 404:
        return 4404
    return 4400


def _response_error_code(response: HTTPResponse) -> str:
    body = response.body
    if isinstance(body, dict):
        code = body.get("code")
        if isinstance(code, str) and code:
            return code
        error = body.get("error")
        if isinstance(error, dict):
            nested = error.get("code")
            if isinstance(nested, str) and nested:
                return nested
    return "stream request rejected"


__all__ = [
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "TERMINAL_COLLECTION",
    "TERMINAL_COMMANDS",
    "build_openapi",
]
