from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
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
    SessionStatus,
    SessionType,
    TerminalFrame,
    TerminalSessionService,
)


class TrackingReferenceTerminalAdapter(ReferenceTerminalAdapter):
    def __init__(self) -> None:
        super().__init__(poll_interval_seconds=0.001)
        self.last_handle: AdapterSessionHandle | None = None

    async def start(self, request: SessionCreateRequest):
        result = await super().start(request)
        self.last_handle = result.handle
        return result


def _policy(
    principal: str,
    project_id: str,
    workspace_id: str,
    *,
    approval_actions: frozenset[AuthorizationAction] = frozenset(),
    allowed_actions: frozenset[AuthorizationAction] | None = None,
) -> LocalPrincipalPolicy:
    return LocalPrincipalPolicy(
        principal_ref=principal,
        actor_types=frozenset({ActorType.HUMAN}),
        allowed_actions=allowed_actions
        if allowed_actions is not None
        else frozenset(
            {
                AuthorizationAction.READ,
                AuthorizationAction.CREATE,
                AuthorizationAction.EXECUTE,
                AuthorizationAction.MODIFY,
            }
        ),
        approval_actions=approval_actions,
        resource_types=frozenset({ResourceType.GENERIC}),
        project_ids=frozenset({project_id}),
        workspace_ids=frozenset({workspace_id}),
    )


def test_manual_interactive_session_can_resume_after_exact_approval() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:terminal-operator"
    reviewer = "user:reviewer"
    provider = LocalAuthorizationProvider(
        (
            _policy(
                principal,
                project_id,
                workspace_id,
                approval_actions=frozenset({AuthorizationAction.CREATE}),
            ),
            LocalPrincipalPolicy(
                principal_ref=reviewer,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                resource_types=frozenset({ResourceType.GENERIC}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    service = TerminalSessionService(gate, (ReferenceTerminalAdapter(poll_interval_seconds=0.001),))
    operation = OperationContext(correlation_id="corr-terminal-approval", project_id=project_id)
    request = SessionCreateRequest(
        session_id=new_id("terminal_session"),
        session_type=SessionType.MANUAL,
        context=SessionContext(project_id=project_id, workspace_id=workspace_id),
        mode=SessionMode.INTERACTIVE,
        actor_ref=principal,
        operation=operation,
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(service.create_session(request))
    assert captured.value.code is ErrorCode.FORBIDDEN
    approval_id = captured.value.details.get("approval_id")
    assert isinstance(approval_id, str)
    assert captured.value.details.get("session_id") == request.session_id

    asyncio.run(
        gate.decide_approval(
            approval_id,
            approver=ActorIdentity(reviewer, ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="corr-terminal-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
    )
    session = asyncio.run(service.create_session(request, approval_id=approval_id))

    assert session.id == request.session_id
    assert session.status is SessionStatus.RUNNING
    assert session.mode is SessionMode.INTERACTIVE


def test_workspace_policy_prevents_cross_workspace_session_creation() -> None:
    project_id = new_id("project")
    allowed_workspace = new_id("workspace")
    denied_workspace = new_id("workspace")
    principal = "user:workspace-bound"
    gate = AuthorizationGate(
        LocalAuthorizationProvider((_policy(principal, project_id, allowed_workspace),))
    )
    service = TerminalSessionService(gate, (ReferenceTerminalAdapter(poll_interval_seconds=0.001),))
    operation = OperationContext(correlation_id="corr-workspace-boundary", project_id=project_id)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.create_session(
                SessionCreateRequest(
                    session_type=SessionType.LOG_STREAM,
                    context=SessionContext(
                        project_id=project_id,
                        workspace_id=denied_workspace,
                    ),
                    mode=SessionMode.READ_ONLY,
                    actor_ref=principal,
                    operation=operation,
                )
            )
        )

    assert captured.value.code is ErrorCode.FORBIDDEN


def test_backend_worker_loss_becomes_explicit_canonical_lost_session() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:loss-test"
    gate = AuthorizationGate(
        LocalAuthorizationProvider((_policy(principal, project_id, workspace_id),))
    )
    adapter = TrackingReferenceTerminalAdapter()
    service = TerminalSessionService(gate, (adapter,))
    operation = OperationContext(correlation_id="corr-worker-loss", project_id=project_id)
    session = asyncio.run(
        service.create_session(
            SessionCreateRequest(
                session_type=SessionType.PROCESS,
                context=SessionContext(
                    project_id=project_id,
                    workspace_id=workspace_id,
                    worker_id=new_id("worker"),
                    node_id=new_id("node"),
                ),
                mode=SessionMode.READ_ONLY,
                actor_ref=principal,
                operation=operation,
            )
        )
    )
    assert adapter.last_handle is not None

    adapter.lose(adapter.last_handle, "worker transport lost")
    reconciled = asyncio.run(service.reconcile_session(session.id))
    frames = asyncio.run(
        service.read_frames(
            session.id,
            actor_ref=principal,
            operation=operation,
        )
    )

    assert reconciled.status is SessionStatus.LOST
    assert reconciled.ended_at is not None
    assert frames[-1].final is True
    assert frames[-1].data == "worker transport lost\n"


def test_inactivity_timeout_terminates_session_and_is_auditable() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:timeout-test"
    now = [datetime(2026, 9, 3, 12, 0, tzinfo=UTC)]
    gate = AuthorizationGate(
        LocalAuthorizationProvider((_policy(principal, project_id, workspace_id),))
    )
    adapter = TrackingReferenceTerminalAdapter()
    service = TerminalSessionService(gate, (adapter,), clock=lambda: now[0])
    operation = OperationContext(correlation_id="corr-timeout", project_id=project_id)
    session = asyncio.run(
        service.create_session(
            SessionCreateRequest(
                session_type=SessionType.PROCESS,
                context=SessionContext(project_id=project_id, workspace_id=workspace_id),
                mode=SessionMode.READ_ONLY,
                actor_ref=principal,
                operation=operation,
                inactivity_timeout_seconds=60,
            )
        )
    )

    now[0] += timedelta(seconds=61)
    reconciled = asyncio.run(service.reconcile_session(session.id))
    frames = asyncio.run(service.read_frames(session.id, actor_ref=principal, operation=operation))

    assert reconciled.status is SessionStatus.CANCELLED
    assert reconciled.ended_at == now[0]
    assert frames[-1].final is True
    assert "inactivity timeout" in frames[-1].data
    timeout_records = [
        record
        for record in service.activity_records
        if record.action == "session.inactivity_timeout"
    ]
    assert len(timeout_records) == 1
    assert timeout_records[0].actor_ref == "service:terminal-policy"
    assert timeout_records[0].metadata == {"timeout_seconds": 60}


def test_transcript_retention_uses_redacted_canonical_frames_once_per_sequence() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:retention-test"
    retained: list[TerminalFrame] = []
    gate = AuthorizationGate(
        LocalAuthorizationProvider((_policy(principal, project_id, workspace_id),))
    )
    service = TerminalSessionService(
        gate,
        (ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
        redactor=lambda text: text.replace("secret-token", "[REDACTED]"),
        transcript_sink=retained.append,
    )
    operation = OperationContext(correlation_id="corr-retention", project_id=project_id)
    session = asyncio.run(
        service.create_session(
            SessionCreateRequest(
                session_type=SessionType.DEBUG,
                context=SessionContext(project_id=project_id, workspace_id=workspace_id),
                mode=SessionMode.INTERACTIVE,
                actor_ref=principal,
                operation=operation,
                retain_transcript=True,
            )
        )
    )

    initial = asyncio.run(service.read_frames(session.id, actor_ref=principal, operation=operation))
    asyncio.run(
        service.send_input(
            session.id,
            "secret-token",
            actor_ref=principal,
            operation=operation,
        )
    )
    after_input = asyncio.run(
        service.read_frames(
            session.id,
            actor_ref=principal,
            operation=operation,
            after_sequence=initial[-1].sequence,
        )
    )
    replay = asyncio.run(service.read_frames(session.id, actor_ref=principal, operation=operation))

    assert after_input[-1].data == "[REDACTED]"
    assert "secret-token" not in repr(retained)
    assert [frame.sequence for frame in retained] == [frame.sequence for frame in replay]
    assert len({frame.id for frame in retained}) == len(retained)


def test_transcript_retention_fails_closed_without_evidence_sink() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:no-retention-sink"
    gate = AuthorizationGate(
        LocalAuthorizationProvider((_policy(principal, project_id, workspace_id),))
    )
    service = TerminalSessionService(
        gate,
        (ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
    )
    operation = OperationContext(correlation_id="corr-no-retention-sink", project_id=project_id)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.create_session(
                SessionCreateRequest(
                    session_type=SessionType.LOG_STREAM,
                    context=SessionContext(project_id=project_id, workspace_id=workspace_id),
                    mode=SessionMode.READ_ONLY,
                    actor_ref=principal,
                    operation=operation,
                    retain_transcript=True,
                )
            )
        )

    assert captured.value.code is ErrorCode.UNAVAILABLE


def test_canonical_session_and_stream_payloads_do_not_leak_backend_private_handle_types() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:no-private-leak"
    gate = AuthorizationGate(
        LocalAuthorizationProvider((_policy(principal, project_id, workspace_id),))
    )
    service = TerminalSessionService(
        gate,
        (ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
    )
    operation = OperationContext(correlation_id="corr-private-leak", project_id=project_id)
    session = asyncio.run(
        service.create_session(
            SessionCreateRequest(
                session_type=SessionType.DEBUG,
                context=SessionContext(project_id=project_id, workspace_id=workspace_id),
                mode=SessionMode.INTERACTIVE,
                actor_ref=principal,
                operation=operation,
            )
        )
    )
    frames = asyncio.run(service.read_frames(session.id, actor_ref=principal, operation=operation))
    attachment = asyncio.run(service.attach(session.id, actor_ref=principal, operation=operation))
    serialized = repr(
        {
            "session": session.to_json(),
            "frames": [frame.to_json() for frame in frames],
            "attachment": attachment.to_json(),
        }
    )

    assert "reference-session-" not in serialized
    assert "AdapterSessionHandle" not in serialized
    assert "/dev/pts" not in serialized
