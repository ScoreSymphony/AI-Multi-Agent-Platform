"""Canonical Terminal composition using explicit Control Plane module ownership (#982)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationRequest,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, OperationControl
from ai_multi_agent_platform.security.authorization import infer_actor_identity
from ai_multi_agent_platform.terminal import SessionContext, TerminalSessionService
from ai_multi_agent_platform.workspaces import WorkspaceProvider

from .authenticated_authorization import ControlPlane as _BaseControlPlane
from .automation_api import ControlPlaneHTTP, build_openapi
from .extensions import _validate_resources
from .http import ControlPlaneASGI as _BaseControlPlaneASGI
from .models import PageQuery, RequestContext, paginate
from .module_registry import install_control_plane_modules
from .terminal_composition import (
    _TERMINAL_STREAM_CONTEXT,
    TERMINAL_COLLECTION,
    TERMINAL_COMMANDS,
    _AuthorizedStreamSessions,
    _PreparedTerminalStreamASGI,
)
from .terminal_module import terminal_control_plane_module
from .terminal_session_contract import TerminalSessionASGI


class ControlPlane(_BaseControlPlane):
    """Terminal façade whose resources and commands are owned by one explicit module."""

    def __init__(
        self,
        *args: Any,
        terminal_sessions: TerminalSessionService | None = None,
        **kwargs: Any,
    ) -> None:
        workspace_provider = cast(WorkspaceProvider | None, kwargs.get("workspace_provider"))
        super().__init__(*args, **kwargs)
        self._terminal_sessions = terminal_sessions
        if terminal_sessions is None:
            return
        install_control_plane_modules(
            self,
            (
                terminal_control_plane_module(
                    terminal_sessions,
                    kernel=self._kernel,
                    workspace_provider=workspace_provider,
                    run_canceller=self._cancel_run_from_terminal,
                    authorize=self._authorize_terminal,
                    resolve_scope=self._terminal_scope,
                ),
            ),
        )

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
        await self._authorize_terminal(
            context,
            "terminal-session:list",
            collection,
            project_id=filters.get("project_id"),
            workspace_id=filters.get("workspace_id"),
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
        """Authorize Terminal against its canonical project/workspace and trust context."""

        provider = self._authorization
        if provider is None:
            return
        actor_type = context.actor.actor_type
        if actor_type is None:
            actor_type = infer_actor_identity(context.actor.principal_ref).actor_type.value
        decision = await provider.authorize(
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


class ControlPlaneASGI:
    """Standard ASGI composition including the explicitly owned Terminal WebSocket."""

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


__all__ = [
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "TERMINAL_COLLECTION",
    "TERMINAL_COMMANDS",
    "build_openapi",
]
