from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.browser import StdlibBrowserProvider
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import LocalFileProvider
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


def _terminal_stack(
    *,
    project_id: str,
    workspace_id: str,
    principal: str,
) -> TerminalSessionService:
    policy = LocalPrincipalPolicy(
        principal_ref=principal,
        actor_types=frozenset({ActorType.HUMAN}),
        allowed_actions=frozenset(
            {
                AuthorizationAction.READ,
                AuthorizationAction.CREATE,
                AuthorizationAction.EXECUTE,
                AuthorizationAction.MODIFY,
            }
        ),
        resource_types=frozenset({ResourceType.GENERIC}),
        project_ids=frozenset({project_id}),
        workspace_ids=frozenset({workspace_id}),
    )
    return TerminalSessionService(
        AuthorizationGate(LocalAuthorizationProvider((policy,))),
        (ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
    )


def test_terminal_session_handle_is_not_restored_into_new_process_owner() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        principal = "user:terminal-restart"
        operation = OperationContext(correlation_id="terminal-restart", project_id=project_id)
        context = SessionContext(project_id=project_id, workspace_id=workspace_id)
        original = _terminal_stack(
            project_id=project_id,
            workspace_id=workspace_id,
            principal=principal,
        )
        created = await original.create_session(
            SessionCreateRequest(
                session_type=SessionType.EXECUTION,
                context=context,
                mode=SessionMode.READ_ONLY,
                actor_ref=principal,
                operation=operation,
            )
        )

        restarted = _terminal_stack(
            project_id=project_id,
            workspace_id=workspace_id,
            principal=principal,
        )
        with pytest.raises(ContractError) as exc_info:
            await restarted.get_session(
                created.id,
                actor_ref=principal,
                operation=operation,
            )

        assert exc_info.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_reference_browser_does_not_resolve_pre_restart_process_session_id(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        restarted = StdlibBrowserProvider(files)
        operation = OperationContext(
            correlation_id="browser-restart",
            project_id=new_id("project"),
        )
        stale_session_id = new_id("browser_session")

        with pytest.raises(ContractError) as exc_info:
            await restarted.get_session(stale_session_id, operation)

        assert exc_info.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())
