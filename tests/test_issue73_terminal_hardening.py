from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.control_plane.terminal_session_contract import (
    terminal_command_handlers,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.terminal import (
    ReferenceTerminalAdapter,
    SessionContext,
    SessionCreateRequest,
    SessionMode,
    SessionType,
    TerminalSessionService,
)
from ai_multi_agent_platform.workspaces import WorkspaceProvider


@dataclass(frozen=True)
class _WorkspaceView:
    project_id: str


class _WorkspaceLookup:
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id

    async def get_workspace(self, workspace_id: str) -> _WorkspaceView:
        del workspace_id
        return _WorkspaceView(project_id=self.project_id)


def _service(*, project_id: str, workspace_id: str, principal: str) -> TerminalSessionService:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=principal,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.CREATE}),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project_id}),
                workspace_ids=frozenset({workspace_id}),
            ),
        )
    )
    return TerminalSessionService(
        AuthorizationGate(provider),
        (ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
    )


def test_terminal_exposes_only_explicit_public_adapter_diagnostics() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:terminal-diagnostics"
    service = _service(project_id=project_id, workspace_id=workspace_id, principal=principal)
    session = asyncio.run(
        service.create_session(
            SessionCreateRequest(
                session_type=SessionType.DEBUG,
                context=SessionContext(project_id=project_id, workspace_id=workspace_id),
                mode=SessionMode.READ_ONLY,
                actor_ref=principal,
                operation=OperationContext(
                    correlation_id="corr-terminal-diagnostics",
                    project_id=project_id,
                ),
            )
        )
    )

    assert session.adapter_metadata[0].namespace == "reference-terminal.private"
    payload = session.to_json()
    assert payload["diagnostics"] == [
        {
            "namespace": "reference-terminal",
            "values": {"arbitrary_host_shell": False, "deterministic": True},
        }
    ]
    assert "backend_handle_kind" not in repr(payload)
    assert "reference-terminal.private" not in repr(payload)


def test_terminal_create_rejects_workspace_from_another_project() -> None:
    requested_project = new_id("project")
    actual_project = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:terminal-workspace"
    service = _service(
        project_id=requested_project,
        workspace_id=workspace_id,
        principal=principal,
    )
    handlers = terminal_command_handlers(
        service,
        workspace_provider=cast(WorkspaceProvider, _WorkspaceLookup(actual_project)),
    )
    context = RequestContext(
        request_id="request-terminal-workspace",
        correlation_id="corr-terminal-workspace",
        actor=ActorContext(principal_ref=principal),
        idempotency_key="terminal-workspace-create",
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            handlers["terminal.session.create"](
                context,
                requested_project,
                {
                    "workspace_id": workspace_id,
                    "session_type": "debug",
                    "mode": "read_only",
                },
            )
        )

    assert captured.value.code is ErrorCode.INVALID_REQUEST
    assert captured.value.details == {
        "workspace_id": workspace_id,
        "project_id": requested_project,
    }


def test_run_linked_terminal_context_requires_task_id() -> None:
    with pytest.raises(ValueError, match="requires task_id"):
        SessionContext(
            project_id=new_id("project"),
            workspace_id=new_id("workspace"),
            run_id=new_id("run"),
        )
