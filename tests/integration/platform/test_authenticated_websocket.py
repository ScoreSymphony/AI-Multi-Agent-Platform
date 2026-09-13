from __future__ import annotations

import asyncio
import json
from typing import Any

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane import (
    AuthenticatedControlPlaneHTTP,
    ControlPlane,
    ControlPlaneASGI,
    HTTPRequest,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    ControlPlaneAuthorizationBridge,
    CredentialScope,
    LocalAuthenticationService,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    ScryptPasswordHasher,
)
from ai_multi_agent_platform.terminal import (
    ReferenceTerminalAdapter,
    SessionContext,
    SessionCreateRequest,
    SessionMode,
    SessionType,
    TerminalSessionService,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

PASSWORD = "correct horse battery staple"


def test_authenticated_terminal_websocket_rejects_spoofed_actor() -> None:
    async def scenario() -> None:
        auth = LocalAuthenticationService(
            password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
        )
        user = auth.bootstrap_first_admin("terminal-user", PASSWORD)
        token = auth.create_personal_access_token(user.user_id, purpose="terminal-stream")

        project_id = new_id("project")
        workspace_id = new_id("workspace")
        terminal_policy = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=user.user_id,
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
                ),
            )
        )
        terminal = TerminalSessionService(
            AuthorizationGate(terminal_policy),
            (ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
        )
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            terminal_sessions=terminal,
        )
        app = ControlPlaneASGI(
            AuthenticatedControlPlaneHTTP(control_plane, auth, secure_cookie=False)
        )

        operation = OperationContext(
            correlation_id="terminal-authenticated-stream",
            project_id=project_id,
        )
        session = await terminal.create_session(
            SessionCreateRequest(
                session_type=SessionType.LOG_STREAM,
                context=SessionContext(project_id=project_id, workspace_id=workspace_id),
                mode=SessionMode.READ_ONLY,
                actor_ref=user.user_id,
                operation=operation,
            )
        )
        await terminal.terminate(
            session.id,
            actor_ref=user.user_id,
            operation=operation,
            reason="make authenticated stream deterministic",
        )

        anonymous = await _run_websocket(
            app,
            session.id,
            {
                "x-principal-ref": "user:spoofed",
                "x-owner-type": "user",
                "x-owner-id": "spoofed",
            },
        )
        assert anonymous == [
            {
                "type": "websocket.close",
                "code": 4401,
                "reason": "unauthorized",
            }
        ]

        authenticated = await _run_websocket(
            app,
            session.id,
            {
                "authorization": f"Bearer {token.secret}",
                "x-principal-ref": "user:spoofed",
                "x-owner-type": "user",
                "x-owner-id": "spoofed",
            },
        )
        assert authenticated[0]["type"] == "websocket.accept"
        assert authenticated[0]["subprotocol"] == "platform.terminal.v1"
        attach_records = [
            record for record in terminal.activity_records if record.action == "session.attach"
        ]
        assert attach_records
        assert attach_records[-1].actor_ref == user.user_id
        assert all(record.actor_ref != "user:spoofed" for record in attach_records)

    asyncio.run(scenario())


def test_terminal_credential_scope_limits_http_and_websocket_actions() -> None:
    async def scenario() -> None:
        auth = LocalAuthenticationService(
            password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
        )
        user = auth.bootstrap_first_admin("terminal-scoped-user", PASSWORD)
        token = auth.create_personal_access_token(
            user.user_id,
            purpose="terminal-read-only",
            scope=CredentialScope(
                actions=frozenset({AuthorizationAction.READ}),
                resource_types=frozenset({ResourceType.GENERIC}),
            ),
        )

        project_id = new_id("project")
        workspace_id = new_id("workspace")
        policy = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=user.user_id,
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
                ),
            )
        )
        gate = AuthorizationGate(policy)
        terminal = TerminalSessionService(
            gate,
            (ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
        )
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=ControlPlaneAuthorizationBridge(gate),
            terminal_sessions=terminal,
        )
        http = AuthenticatedControlPlaneHTTP(control_plane, auth, secure_cookie=False)
        app = ControlPlaneASGI(http)

        operation = OperationContext(
            correlation_id="terminal-scoped-stream",
            project_id=project_id,
        )
        session = await terminal.create_session(
            SessionCreateRequest(
                session_type=SessionType.DEBUG,
                context=SessionContext(project_id=project_id, workspace_id=workspace_id),
                mode=SessionMode.INTERACTIVE,
                actor_ref=user.user_id,
                operation=operation,
            )
        )

        loaded = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/terminal-sessions/{session.id}",
                headers={"authorization": f"Bearer {token.secret}"},
            )
        )
        assert loaded.status == 200

        denied_http = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/terminal.session.input",
                headers={
                    "authorization": f"Bearer {token.secret}",
                    "content-type": "application/json",
                    "idempotency-key": "terminal-scope-input",
                },
                body={"resource_ref": session.id, "data": "blocked-http\n"},
            )
        )
        assert denied_http.status == 403
        assert isinstance(denied_http.body, dict)
        assert denied_http.body["category"] == "authorization"

        websocket_messages = await _run_websocket(
            app,
            session.id,
            {"authorization": f"Bearer {token.secret}"},
            client_messages=[
                {
                    "type": "websocket.receive",
                    "text": json.dumps({"type": "input", "data": "blocked-websocket\n"}),
                },
                {"type": "websocket.disconnect"},
            ],
        )
        assert websocket_messages[0]["type"] == "websocket.accept"
        websocket_payloads = [
            json.loads(message["text"])
            for message in websocket_messages
            if message.get("type") == "websocket.send"
        ]
        assert any(
            payload.get("type") == "error" and payload.get("code") == "forbidden"
            for payload in websocket_payloads
        )
        assert all(record.action != "session.input" for record in terminal.activity_records)

    asyncio.run(scenario())


async def _run_websocket(
    app: ControlPlaneASGI,
    session_id: str,
    headers: dict[str, str],
    *,
    client_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    inbound = [{"type": "websocket.connect"}, *(client_messages or [])]
    blocker: asyncio.Future[dict[str, Any]] = asyncio.Future()

    async def receive() -> dict[str, Any]:
        if inbound:
            return inbound.pop(0)
        return await blocker

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in {
            "x-request-id": "request-terminal-auth-ws",
            "x-correlation-id": "correlation-terminal-auth-ws",
            **headers,
        }.items()
    ]
    await app(
        {
            "type": "websocket",
            "path": f"/api/v1/terminal-sessions/{session_id}/stream",
            "query_string": b"",
            "headers": scope_headers,
            "subprotocols": ["platform.terminal.v1"],
        },
        receive,
        send,
    )
    return sent
