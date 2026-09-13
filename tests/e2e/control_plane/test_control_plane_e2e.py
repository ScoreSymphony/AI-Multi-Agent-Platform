from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane import (
    ControlPlane,
    ControlPlaneASGI,
    ControlPlaneHTTP,
    HTTPRequest,
)
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.terminal import (
    AdapterSessionHandle,
    ReferenceTerminalAdapter,
    SessionContext,
    SessionCreateRequest,
    SessionMode,
    SessionType,
    TerminalFrame,
    TerminalSessionService,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class TrackingReferenceTerminalAdapter(ReferenceTerminalAdapter):
    def __init__(self) -> None:
        super().__init__(poll_interval_seconds=0.001)
        self.last_handle: AdapterSessionHandle | None = None

    async def start(self, request: SessionCreateRequest):
        result = await super().start(request)
        self.last_handle = result.handle
        return result


def _terminal_stack() -> tuple[
    TerminalSessionService,
    TrackingReferenceTerminalAdapter,
    PlatformKernel,
    ControlPlaneHTTP,
    ControlPlaneASGI,
    str,
    str,
    str,
]:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:terminal-e2e"
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
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
            ),
        )
    )
    adapter = TrackingReferenceTerminalAdapter()
    service = TerminalSessionService(AuthorizationGate(authorization), (adapter,))
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        terminal_sessions=service,
    )
    http = ControlPlaneHTTP(control_plane)
    app = ControlPlaneASGI(http)
    return service, adapter, kernel, http, app, principal, project_id, workspace_id


def _headers(principal: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-terminal-e2e",
        "X-Correlation-Id": "correlation-terminal-e2e",
        "X-Principal-Ref": principal,
        "X-Owner-Type": "user",
        "X-Owner-Id": "terminal-e2e",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def test_terminal_http_resource_and_command_use_standard_composition_and_idempotent_create() -> (
    None
):
    async def scenario() -> None:
        _, _, _, http, _, principal, project_id, workspace_id = _terminal_stack()
        request = HTTPRequest(
            method="POST",
            path="/api/v1/commands/terminal.session.create",
            headers=_headers(principal, key="terminal-create-e2e"),
            body={
                "resource_ref": project_id,
                "workspace_id": workspace_id,
                "session_type": "debug",
                "mode": "interactive",
            },
        )
        created = await http.handle(request)
        retried = await http.handle(request)
        assert created.status == 200
        assert retried.status == 200
        assert isinstance(created.body, dict)
        assert isinstance(retried.body, dict)
        session_id = created.body["id"]
        assert isinstance(session_id, str)
        assert retried.body["id"] == session_id
        assert created.body["project_id"] == project_id
        assert created.body["workspace_id"] == workspace_id
        assert created.body["context"] == {
            "project_id": project_id,
            "workspace_id": workspace_id,
            "task_id": None,
            "run_id": None,
            "worker_id": None,
            "node_id": None,
        }
        assert "adapter_metadata" not in created.body
        assert "backend_handle_kind" not in repr(created.body)
        assert isinstance(created.body["diagnostics"], list)

        conflicting_retry = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/terminal.session.create",
                headers=_headers(principal, key="terminal-create-e2e"),
                body={
                    "resource_ref": project_id,
                    "workspace_id": workspace_id,
                    "session_type": "debug",
                    "mode": "read_only",
                },
            )
        )
        assert conflicting_retry.status == 409

        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/terminal-sessions",
                headers=_headers(principal),
                query={
                    "filter[project_id]": project_id,
                    "filter[workspace_id]": workspace_id,
                    "filter[status]": "running",
                },
            )
        )
        assert listed.status == 200
        assert isinstance(listed.body, dict)
        assert listed.body["total"] == 1
        assert isinstance(listed.body["items"], list)
        assert listed.body["items"][0]["id"] == session_id

        loaded = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/terminal-sessions/{session_id}",
                headers=_headers(principal),
            )
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["id"] == session_id

        terminated = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/terminal.session.terminate",
                headers=_headers(principal, key="terminal-terminate-e2e"),
                body={"resource_ref": session_id, "reason": "e2e complete"},
            )
        )
        assert terminated.status == 200
        assert isinstance(terminated.body, dict)
        assert terminated.body["status"] == "cancelled"

    asyncio.run(scenario())


def test_run_bound_process_termination_cancels_canonical_run() -> None:
    async def scenario() -> None:
        _, _, kernel, http, _, principal, project_id, workspace_id = _terminal_stack()
        task = await kernel.create_task(
            idempotency_key="terminal-task-create",
            title="Terminal cancellation",
            objective="Prove canonical cancellation",
            owner_type="user",
            owner_id="terminal-e2e",
            project_id=project_id,
            actor_ref=principal,
        )
        await kernel.ready_task(
            idempotency_key="terminal-task-ready",
            task_id=task.task.id,
            actor_ref=principal,
        )
        run = await kernel.start_task(
            idempotency_key="terminal-task-start",
            task_id=task.task.id,
            actor_ref=principal,
        )
        created = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/terminal.session.create",
                headers=_headers(principal, key="terminal-process-create"),
                body={
                    "resource_ref": project_id,
                    "workspace_id": workspace_id,
                    "session_type": "process",
                    "mode": "read_only",
                    "task_id": task.task.id,
                    "run_id": run.run_id,
                },
            )
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        session_id = created.body["id"]
        assert isinstance(session_id, str)

        terminated = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/terminal.session.terminate",
                headers=_headers(principal, key="terminal-process-terminate"),
                body={"resource_ref": session_id, "reason": "operator cancelled execution"},
            )
        )
        assert terminated.status == 200
        canonical_run = await kernel.get_run(task.task.id, run.run_id)
        assert canonical_run.status is RunStatus.CANCELLED

    asyncio.run(scenario())


def test_terminal_websocket_stream_and_reconnect_use_standard_gateway() -> None:
    async def scenario() -> None:
        service, adapter, _, _, app, principal, project_id, workspace_id = _terminal_stack()
        session = await service.create_session(
            SessionCreateRequest(
                session_type=SessionType.DEBUG,
                context=SessionContext(project_id=project_id, workspace_id=workspace_id),
                mode=SessionMode.INTERACTIVE,
                actor_ref=principal,
                operation=OperationContext(
                    correlation_id="correlation-terminal-ws",
                    project_id=project_id,
                ),
            )
        )
        assert adapter.last_handle is not None
        await adapter.send_input(adapter.last_handle, "streamed output\n")
        adapter.complete(adapter.last_handle)

        first_messages = await _run_websocket(app, session.id, principal, after_sequence=0)
        first_payloads = _websocket_payloads(first_messages)
        assert first_messages[0]["type"] == "websocket.accept"
        assert first_messages[0]["subprotocol"] == "platform.terminal.v1"
        assert first_payloads[0]["type"] == "session.snapshot"
        frames = [
            payload["frame"] for payload in first_payloads if payload["type"] == "stream.frame"
        ]
        assert [frame["sequence"] for frame in frames] == [1, 2, 3]
        assert frames[1]["data"] == "streamed output\n"
        assert first_payloads[-1]["type"] == "session.status"
        assert first_payloads[-1]["session"]["status"] == "completed"

        reconnect_messages = await _run_websocket(app, session.id, principal, after_sequence=2)
        reconnect_payloads = _websocket_payloads(reconnect_messages)
        replayed = [
            payload["frame"] for payload in reconnect_payloads if payload["type"] == "stream.frame"
        ]
        assert len(replayed) == 1
        assert replayed[0]["sequence"] == 3
        assert replayed[0]["id"] == frames[2]["id"]

    asyncio.run(scenario())


def test_terminal_websocket_rejects_missing_required_subprotocol() -> None:
    async def scenario() -> None:
        service, _, _, _, app, principal, project_id, workspace_id = _terminal_stack()
        session = await service.create_session(
            SessionCreateRequest(
                session_type=SessionType.LOG_STREAM,
                context=SessionContext(project_id=project_id, workspace_id=workspace_id),
                mode=SessionMode.READ_ONLY,
                actor_ref=principal,
                operation=OperationContext(
                    correlation_id="corr-ws-protocol", project_id=project_id
                ),
            )
        )
        messages = await _run_websocket(
            app,
            session.id,
            principal,
            after_sequence=0,
            subprotocols=[],
        )
        assert messages == [
            {
                "type": "websocket.close",
                "code": 4406,
                "reason": "terminal subprotocol required",
            }
        ]

    asyncio.run(scenario())


def test_transcript_sink_failure_is_retryable_without_changing_canonical_frame_identity() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:terminal-retention-retry"
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=principal,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.READ, AuthorizationAction.CREATE}),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project_id}),
                workspace_ids=frozenset({workspace_id}),
            ),
        )
    )
    retained: list[TerminalFrame] = []
    attempts = 0

    def flaky_sink(frame: TerminalFrame) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary evidence outage")
        retained.append(frame)

    service = TerminalSessionService(
        AuthorizationGate(authorization),
        (ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
        transcript_sink=flaky_sink,
    )
    request = SessionCreateRequest(
        session_id=new_id("terminal_session"),
        session_type=SessionType.LOG_STREAM,
        context=SessionContext(project_id=project_id, workspace_id=workspace_id),
        mode=SessionMode.READ_ONLY,
        actor_ref=principal,
        operation=OperationContext(
            correlation_id="correlation-terminal-retention-retry",
            project_id=project_id,
        ),
        retain_transcript=True,
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(service.create_session(request))
    assert captured.value.code is ErrorCode.UNAVAILABLE

    recovered = asyncio.run(service.create_session(request))
    replay = asyncio.run(
        service.read_frames(
            recovered.id,
            actor_ref=principal,
            operation=request.operation,
        )
    )
    assert attempts == 2
    assert len(retained) == 1
    assert retained[0].sequence == 1
    assert retained[0].id == replay[0].id


async def _run_websocket(
    app: ControlPlaneASGI,
    session_id: str,
    principal: str,
    *,
    after_sequence: int,
    subprotocols: list[str] | None = None,
) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    connected = False
    blocker: asyncio.Future[dict[str, Any]] = asyncio.Future()

    async def receive() -> dict[str, Any]:
        nonlocal connected
        if not connected:
            connected = True
            return {"type": "websocket.connect"}
        return await blocker

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    query_string = b"" if after_sequence == 0 else f"after_sequence={after_sequence}".encode()
    headers = [
        (b"x-principal-ref", principal.encode()),
        (b"x-owner-type", b"user"),
        (b"x-owner-id", b"terminal-e2e"),
        (b"x-request-id", b"request-terminal-ws"),
        (b"x-correlation-id", b"correlation-terminal-ws"),
    ]
    await app(
        {
            "type": "websocket",
            "path": f"/api/v1/terminal-sessions/{session_id}/stream",
            "query_string": query_string,
            "headers": headers,
            "subprotocols": subprotocols if subprotocols is not None else ["platform.terminal.v1"],
        },
        receive,
        send,
    )
    return sent


def _websocket_payloads(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for message in messages:
        if message.get("type") != "websocket.send":
            continue
        payload = json.loads(message["text"])
        assert isinstance(payload, dict)
        payloads.append(payload)
    return payloads
