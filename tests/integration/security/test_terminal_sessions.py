from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
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
    SessionStatus,
    SessionType,
    StreamChannel,
    TerminalSession,
    TerminalSessionService,
)


def _stack(
    *, redactor: Callable[[str], str] | None = None
) -> tuple[
    TerminalSessionService,
    ReferenceTerminalAdapter,
    str,
    OperationContext,
    SessionContext,
]:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    principal = "user:terminal-test"
    provider = LocalAuthorizationProvider(
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
    gate = AuthorizationGate(provider)
    adapter = ReferenceTerminalAdapter(poll_interval_seconds=0.001)
    service = TerminalSessionService(gate, (adapter,), redactor=redactor)
    operation = OperationContext(correlation_id="corr-terminal", project_id=project_id)
    context = SessionContext(project_id=project_id, workspace_id=workspace_id)
    return service, adapter, principal, operation, context


def _create(
    service: TerminalSessionService,
    principal: str,
    operation: OperationContext,
    context: SessionContext,
    *,
    mode: SessionMode = SessionMode.INTERACTIVE,
    session_type: SessionType = SessionType.MANUAL,
) -> TerminalSession:
    return asyncio.run(
        service.create_session(
            SessionCreateRequest(
                session_type=session_type,
                context=context,
                mode=mode,
                actor_ref=principal,
                operation=operation,
            )
        )
    )


def test_reference_session_is_canonical_and_hides_backend_handle() -> None:
    service, _, principal, operation, context = _stack()
    session = _create(service, principal, operation, context)

    assert session.id.startswith("terminal_session_")
    assert session.status is SessionStatus.RUNNING
    assert session.capabilities.interactive_input is True
    payload = session.to_json()
    assert "reference-session-" not in repr(payload)
    assert payload["adapter_id"] == "reference-terminal"


def test_interactive_input_streams_and_reconnect_reuses_canonical_frame_identity() -> None:
    service, _, principal, operation, context = _stack()
    session = _create(service, principal, operation, context)

    initial = asyncio.run(service.read_frames(session.id, actor_ref=principal, operation=operation))
    assert len(initial) == 1
    assert initial[0].channel is StreamChannel.SYSTEM

    asyncio.run(
        service.send_input(
            session.id,
            "hello terminal\n",
            actor_ref=principal,
            operation=operation,
        )
    )
    first_read = asyncio.run(
        service.read_frames(
            session.id,
            actor_ref=principal,
            operation=operation,
            after_sequence=1,
        )
    )
    replay = asyncio.run(
        service.read_frames(
            session.id,
            actor_ref=principal,
            operation=operation,
            after_sequence=1,
        )
    )

    assert [frame.data for frame in first_read] == ["hello terminal\n"]
    assert first_read[0].channel is StreamChannel.STDOUT
    assert replay[0].id == first_read[0].id
    assert replay[0].sequence == 2


def test_read_only_session_rejects_input() -> None:
    service, _, principal, operation, context = _stack()
    session = _create(
        service,
        principal,
        operation,
        context,
        mode=SessionMode.READ_ONLY,
        session_type=SessionType.LOG_STREAM,
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.send_input(
                session.id,
                "blocked",
                actor_ref=principal,
                operation=operation,
            )
        )
    assert captured.value.code is ErrorCode.FORBIDDEN


def test_stream_output_is_redacted_and_input_audit_does_not_store_content() -> None:
    service, _, principal, operation, context = _stack(
        redactor=lambda text: text.replace("secret-token", "[REDACTED]")
    )
    session = _create(service, principal, operation, context)
    asyncio.run(
        service.send_input(
            session.id,
            "secret-token",
            actor_ref=principal,
            operation=operation,
        )
    )
    frames = asyncio.run(
        service.read_frames(
            session.id,
            actor_ref=principal,
            operation=operation,
            after_sequence=1,
        )
    )

    assert frames[0].data == "[REDACTED]"
    assert "secret-token" not in repr(service.activity_records)
    input_records = [
        record for record in service.activity_records if record.action == "session.input"
    ]
    assert input_records[0].metadata == {"size_bytes": len("secret-token")}


def test_default_terminal_redaction_scrubs_sensitive_environment_assignments() -> None:
    service, _, principal, operation, context = _stack()
    session = _create(service, principal, operation, context)
    raw = (
        "OPENAI_API_KEY=super-secret-value\n"
        "GITHUB_TOKEN=ghp_private_value\n"
        "DATABASE_PASSWORD='database-secret'\n"
        "SAFE_SETTING=visible\n"
    )

    asyncio.run(
        service.send_input(
            session.id,
            raw,
            actor_ref=principal,
            operation=operation,
        )
    )
    frames = asyncio.run(
        service.read_frames(
            session.id,
            actor_ref=principal,
            operation=operation,
            after_sequence=1,
        )
    )

    assert len(frames) == 1
    output = frames[0].data
    assert "super-secret-value" not in output
    assert "ghp_private_value" not in output
    assert "database-secret" not in output
    assert "OPENAI_API_KEY=[REDACTED]" in output
    assert "GITHUB_TOKEN=[REDACTED]" in output
    assert "DATABASE_PASSWORD=[REDACTED]" in output
    assert "SAFE_SETTING=visible" in output


def test_attach_detach_does_not_cancel_underlying_session() -> None:
    service, _, principal, operation, context = _stack()
    session = _create(service, principal, operation, context)
    attachment = asyncio.run(
        service.attach(
            session.id,
            actor_ref=principal,
            operation=operation,
            after_sequence=1,
        )
    )
    detached = asyncio.run(
        service.detach(
            attachment.id,
            actor_ref=principal,
            operation=operation,
        )
    )
    current = asyncio.run(service.get_session(session.id, actor_ref=principal, operation=operation))

    assert detached.status.value == "detached"
    assert current.status is SessionStatus.RUNNING


def test_termination_flows_through_canonical_service_and_emits_final_frame() -> None:
    service, _, principal, operation, context = _stack()
    session = _create(service, principal, operation, context)
    terminated = asyncio.run(
        service.terminate(
            session.id,
            actor_ref=principal,
            operation=operation,
            reason="user requested",
        )
    )
    frames = asyncio.run(
        service.read_frames(
            session.id,
            actor_ref=principal,
            operation=operation,
            after_sequence=1,
        )
    )

    assert terminated.status is SessionStatus.CANCELLED
    assert terminated.ended_at is not None
    assert frames[-1].final is True
    assert "user requested" in frames[-1].data


def test_unregistered_actor_cannot_read_or_attach_session() -> None:
    service, _, principal, operation, context = _stack()
    session = _create(service, principal, operation, context)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.get_session(
                session.id,
                actor_ref="user:unregistered",
                operation=operation,
            )
        )
    assert captured.value.code is ErrorCode.FORBIDDEN
