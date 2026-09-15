"""Explicit Control Plane ownership for canonical Terminal sessions (#73, #982)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.terminal import SessionContext, TerminalSessionService
from ai_multi_agent_platform.workspaces import WorkspaceProvider

from .extensions import CommandAuthorizer, ControlPlaneModule
from .models import RequestContext
from .service import _payload_digest
from .terminal_session_contract import (
    RunCanceller,
    terminal_command_handlers,
    terminal_resource_services,
)

TERMINAL_MODULE = "terminal"


class TerminalAuthorization(Protocol):
    """Narrow authorization dependency required by Terminal northbound adapters."""

    def __call__(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        project_id: str | None,
        workspace_id: str | None,
        request_payload_digest: str | None = None,
    ) -> Awaitable[None]: ...


TerminalScopeResolver = Callable[[str], SessionContext]


def terminal_control_plane_module(
    sessions: TerminalSessionService,
    *,
    kernel: PlatformKernel,
    workspace_provider: WorkspaceProvider | None,
    run_canceller: RunCanceller,
    authorize: TerminalAuthorization,
    resolve_scope: TerminalScopeResolver,
) -> ControlPlaneModule:
    """Build Terminal resource/command ownership with explicit authorization hooks."""

    handlers = terminal_command_handlers(
        sessions,
        kernel=kernel,
        workspace_provider=workspace_provider,
        run_canceller=run_canceller,
    )

    async def authorize_create(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> None:
        workspace_id = payload.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "workspace_id must be a non-blank string",
                details={"field": "workspace_id"},
            )
        await authorize(
            context,
            "terminal.session.create",
            resource_ref,
            project_id=resource_ref,
            workspace_id=workspace_id,
            request_payload_digest=_payload_digest(payload),
        )

    def scoped_authorizer(command: str) -> CommandAuthorizer:
        async def authorize_scoped(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, JsonValue],
        ) -> None:
            scope = resolve_scope(resource_ref)
            await authorize(
                context,
                command,
                resource_ref,
                project_id=scope.project_id,
                workspace_id=scope.workspace_id,
                request_payload_digest=_payload_digest(payload),
            )

        return authorize_scoped

    authorizers: dict[str, CommandAuthorizer] = {
        "terminal.session.create": authorize_create,
        "terminal.session.input": scoped_authorizer("terminal.session.input"),
        "terminal.session.resize": scoped_authorizer("terminal.session.resize"),
        "terminal.session.terminate": scoped_authorizer("terminal.session.terminate"),
    }
    if frozenset(authorizers) != frozenset(handlers):
        raise RuntimeError("explicit Terminal module command inventory is incomplete")

    return ControlPlaneModule(
        name=TERMINAL_MODULE,
        resource_services=terminal_resource_services(sessions),
        command_handlers=handlers,
        command_authorizers=authorizers,
    )


__all__ = [
    "TERMINAL_MODULE",
    "TerminalAuthorization",
    "terminal_control_plane_module",
]
