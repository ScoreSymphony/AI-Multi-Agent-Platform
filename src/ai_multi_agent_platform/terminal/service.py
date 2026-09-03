"""Platform-owned terminal/session lifecycle and authorization service."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.security.authorization import (
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)
from ai_multi_agent_platform.security.enforcement import AuthorizationGate

from .contracts import (
    AdapterFrame,
    AdapterSessionHandle,
    SessionCreateRequest,
    TerminalSessionAdapter,
)
from .models import (
    TERMINAL_SESSION_STATUSES,
    SessionAttachment,
    SessionMode,
    SessionStatus,
    SessionType,
    TerminalDimensions,
    TerminalFrame,
    TerminalSession,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class TerminalActivityRecord:
    session_id: str
    actor_ref: str
    action: str
    correlation_id: str
    occurred_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.session_id, "terminal_session")
        if not self.actor_ref.strip():
            raise ValueError("activity actor_ref must not be blank")
        if not self.action.strip():
            raise ValueError("activity action must not be blank")
        if not self.correlation_id.strip():
            raise ValueError("activity correlation_id must not be blank")


class TerminalSessionService:
    """Own canonical sessions while adapters remain replaceable implementation details."""

    def __init__(
        self,
        authorization: AuthorizationGate,
        adapters: tuple[TerminalSessionAdapter, ...],
        *,
        redactor: Callable[[str], str] | None = None,
    ) -> None:
        if not adapters:
            raise ValueError("terminal service requires at least one session adapter")
        self._authorization = authorization
        self._adapters: dict[str, TerminalSessionAdapter] = {}
        for adapter in adapters:
            adapter_id = adapter.descriptor.adapter_id
            if adapter_id in self._adapters:
                raise ValueError(f"duplicate terminal adapter: {adapter_id}")
            self._adapters[adapter_id] = adapter
        self._redact = redactor or (lambda value: value)
        self._sessions: dict[str, TerminalSession] = {}
        self._handles: dict[str, AdapterSessionHandle] = {}
        self._frames: dict[str, dict[int, TerminalFrame]] = {}
        self._attachments: dict[str, SessionAttachment] = {}
        self._activity: list[TerminalActivityRecord] = []

    @property
    def activity_records(self) -> tuple[TerminalActivityRecord, ...]:
        return tuple(self._activity)

    async def create_session(
        self,
        request: SessionCreateRequest,
        *,
        approval_id: str | None = None,
    ) -> TerminalSession:
        adapter = self._adapters.get(request.adapter_id)
        if adapter is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"terminal adapter is not registered: {request.adapter_id}",
            )

        risk = (
            RiskClassification.HIGH
            if request.session_type is SessionType.MANUAL
            and request.mode is SessionMode.INTERACTIVE
            else RiskClassification.ELEVATED
        )
        try:
            await self._enforce(
                actor_ref=request.actor_ref,
                operation=request.operation,
                action=AuthorizationAction.CREATE,
                resource_id=request.session_id,
                workspace_id=request.context.workspace_id,
                task_id=request.context.task_id,
                run_id=request.context.run_id,
                node_id=request.context.node_id,
                side_effect=(
                    "manual_terminal_session"
                    if request.session_type is SessionType.MANUAL
                    else "execution_session_create"
                ),
                security_labels=("terminal_session", *request.policy_classification),
                payload={
                    "session_type": request.session_type.value,
                    "mode": request.mode.value,
                    "project_id": request.context.project_id,
                    "workspace_id": request.context.workspace_id,
                    "adapter_id": request.adapter_id,
                },
                approval_id=approval_id,
                risk=risk,
            )
        except ContractError as exc:
            if exc.code is ErrorCode.FORBIDDEN:
                details = dict(exc.details)
                details.setdefault("session_id", request.session_id)
                raise ContractError(
                    exc.code,
                    exc.message,
                    retryable=exc.retryable,
                    provider_id=exc.provider_id,
                    details=details,
                    adapter_metadata=exc.adapter_metadata,
                ) from exc
            raise

        existing = self._sessions.get(request.session_id)
        if existing is not None:
            if (
                existing.owner_actor_ref == request.actor_ref
                and existing.context == request.context
                and existing.session_type is request.session_type
                and existing.mode is request.mode
                and existing.adapter_id == request.adapter_id
                and existing.dimensions == request.dimensions
                and existing.encoding == request.encoding
                and existing.policy_classification == request.policy_classification
            ):
                return existing
            raise ContractError(
                ErrorCode.CONFLICT,
                "terminal session id is already bound to a different create request",
            )

        started = await adapter.start(request)
        session = TerminalSession(
            id=request.session_id,
            session_type=request.session_type,
            context=request.context,
            mode=request.mode,
            owner_actor_ref=request.actor_ref,
            adapter_id=request.adapter_id,
            capabilities=started.capabilities,
            status=SessionStatus.STARTING,
            encoding=request.encoding,
            dimensions=request.dimensions,
            policy_classification=request.policy_classification,
            adapter_metadata=started.metadata,
        )
        if started.status is not SessionStatus.STARTING:
            session = session.transition(started.status)
        self._sessions[session.id] = session
        self._handles[session.id] = started.handle
        self._frames[session.id] = {}
        self._record(session.id, request.actor_ref, "session.create", request.operation)
        return session

    async def list_sessions(
        self,
        *,
        actor_ref: str,
        operation: OperationContext,
        workspace_id: str | None = None,
    ) -> tuple[TerminalSession, ...]:
        await self._enforce(
            actor_ref=actor_ref,
            operation=operation,
            action=AuthorizationAction.READ,
            resource_id="terminal-sessions",
            workspace_id=workspace_id,
            side_effect="terminal_session_list",
            security_labels=("terminal_session",),
            payload={"workspace_id": workspace_id},
            risk=RiskClassification.STANDARD,
        )
        sessions: list[TerminalSession] = []
        for session_id in tuple(self._sessions):
            session = await self._refresh(session_id)
            if (
                operation.project_id is not None
                and session.context.project_id != operation.project_id
            ):
                continue
            if workspace_id is not None and session.context.workspace_id != workspace_id:
                continue
            sessions.append(session)
        return tuple(sorted(sessions, key=lambda item: (item.started_at, item.id)))

    async def get_session(
        self,
        session_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
    ) -> TerminalSession:
        session = self._session(session_id)
        await self._authorize_session(
            session,
            actor_ref=actor_ref,
            operation=operation,
            action=AuthorizationAction.READ,
            side_effect="terminal_session_read",
            payload=None,
            risk=RiskClassification.STANDARD,
        )
        return await self._refresh(session_id)

    async def attach(
        self,
        session_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
        after_sequence: int = 0,
    ) -> SessionAttachment:
        if after_sequence < 0:
            raise ContractError(ErrorCode.INVALID_REQUEST, "after_sequence must be non-negative")
        session = self._session(session_id)
        await self._authorize_session(
            session,
            actor_ref=actor_ref,
            operation=operation,
            action=AuthorizationAction.READ,
            side_effect="terminal_session_attach",
            payload={"after_sequence": after_sequence},
            risk=RiskClassification.STANDARD,
        )
        attachment = SessionAttachment(
            session_id=session_id,
            actor_ref=actor_ref,
            after_sequence=after_sequence,
        )
        self._attachments[attachment.id] = attachment
        self._record(
            session_id,
            actor_ref,
            "session.attach",
            operation,
            {"attachment_id": attachment.id, "after_sequence": after_sequence},
        )
        return attachment

    async def detach(
        self,
        attachment_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
    ) -> SessionAttachment:
        validate_id(attachment_id, "terminal_attachment")
        try:
            attachment = self._attachments[attachment_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "terminal attachment was not found") from exc
        session = self._session(attachment.session_id)
        await self._authorize_session(
            session,
            actor_ref=actor_ref,
            operation=operation,
            action=AuthorizationAction.READ,
            side_effect="terminal_session_detach",
            payload={"attachment_id": attachment_id},
            risk=RiskClassification.STANDARD,
        )
        if attachment.actor_ref != actor_ref:
            raise ContractError(ErrorCode.FORBIDDEN, "attachment belongs to another actor")
        detached = attachment.detach()
        self._attachments[attachment_id] = detached
        self._record(
            session.id,
            actor_ref,
            "session.detach",
            operation,
            {"attachment_id": attachment_id},
        )
        return detached

    async def read_frames(
        self,
        session_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
        after_sequence: int = 0,
    ) -> tuple[TerminalFrame, ...]:
        session = self._session(session_id)
        await self._authorize_session(
            session,
            actor_ref=actor_ref,
            operation=operation,
            action=AuthorizationAction.READ,
            side_effect="terminal_stream_read",
            payload={"after_sequence": after_sequence},
            risk=RiskClassification.STANDARD,
        )
        adapter = self._adapter(session)
        frames = await adapter.read_frames(
            self._handles[session_id], after_sequence=after_sequence
        )
        return tuple(self._canonical_frame(session_id, frame) for frame in frames)

    async def stream_frames(
        self,
        session_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
        after_sequence: int = 0,
    ) -> AsyncIterator[TerminalFrame]:
        session = self._session(session_id)
        await self._authorize_session(
            session,
            actor_ref=actor_ref,
            operation=operation,
            action=AuthorizationAction.READ,
            side_effect="terminal_stream_attach",
            payload={"after_sequence": after_sequence},
            risk=RiskClassification.STANDARD,
        )
        adapter = self._adapter(session)
        async for frame in adapter.stream_frames(
            self._handles[session_id],
            after_sequence=after_sequence,
        ):
            yield self._canonical_frame(session_id, frame)
        await self._refresh(session_id)

    async def send_input(
        self,
        session_id: str,
        data: str,
        *,
        actor_ref: str,
        operation: OperationContext,
        approval_id: str | None = None,
    ) -> None:
        session = self._session(session_id)
        if (
            session.mode is not SessionMode.INTERACTIVE
            or not session.capabilities.interactive_input
        ):
            raise ContractError(ErrorCode.FORBIDDEN, "terminal session does not accept input")
        await self._authorize_session(
            session,
            actor_ref=actor_ref,
            operation=operation,
            action=AuthorizationAction.EXECUTE,
            side_effect="terminal_input",
            payload={
                "data_sha256": _text_digest(data),
                "size_bytes": len(data.encode(session.encoding)),
            },
            approval_id=approval_id,
            risk=RiskClassification.ELEVATED,
        )
        await self._adapter(session).send_input(self._handles[session_id], data)
        self._record(
            session_id,
            actor_ref,
            "session.input",
            operation,
            {"size_bytes": len(data.encode(session.encoding))},
        )

    async def resize(
        self,
        session_id: str,
        dimensions: TerminalDimensions,
        *,
        actor_ref: str,
        operation: OperationContext,
    ) -> TerminalSession:
        session = self._session(session_id)
        if not session.capabilities.resize:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY, "terminal session does not resize"
            )
        await self._authorize_session(
            session,
            actor_ref=actor_ref,
            operation=operation,
            action=AuthorizationAction.MODIFY,
            side_effect="terminal_resize",
            payload=dimensions.to_json(),
            risk=RiskClassification.STANDARD,
        )
        await self._adapter(session).resize(self._handles[session_id], dimensions)
        updated = session.with_dimensions(dimensions)
        self._sessions[session_id] = updated
        self._record(session_id, actor_ref, "session.resize", operation, dimensions.to_json())
        return updated

    async def terminate(
        self,
        session_id: str,
        *,
        actor_ref: str,
        operation: OperationContext,
        reason: str | None = None,
        approval_id: str | None = None,
    ) -> TerminalSession:
        session = self._session(session_id)
        if session.status in TERMINAL_SESSION_STATUSES:
            return session
        await self._authorize_session(
            session,
            actor_ref=actor_ref,
            operation=operation,
            action=AuthorizationAction.EXECUTE,
            side_effect="terminal_terminate",
            payload={"reason": reason},
            approval_id=approval_id,
            risk=RiskClassification.HIGH,
        )
        await self._adapter(session).terminate(self._handles[session_id], reason=reason)
        updated = await self._refresh(session_id)
        self._record(session_id, actor_ref, "session.terminate", operation)
        return updated

    async def reconcile_session(self, session_id: str) -> TerminalSession:
        """Refresh canonical status after adapter completion or later worker/node loss."""

        return await self._refresh(session_id)

    def _session(self, session_id: str) -> TerminalSession:
        validate_id(session_id, "terminal_session")
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "terminal session was not found") from exc

    def _adapter(self, session: TerminalSession) -> TerminalSessionAdapter:
        try:
            return self._adapters[session.adapter_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE, "terminal session adapter is unavailable"
            ) from exc

    async def _refresh(self, session_id: str) -> TerminalSession:
        session = self._session(session_id)
        backend_status = await self._adapter(session).status(self._handles[session_id])
        if backend_status == session.status:
            return session
        if session.status in TERMINAL_SESSION_STATUSES:
            return session
        try:
            updated = session.transition(backend_status)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "terminal adapter returned an invalid lifecycle transition",
                provider_id=session.adapter_id,
                details={"from": session.status.value, "to": backend_status.value},
            ) from exc
        self._sessions[session_id] = updated
        return updated

    async def _authorize_session(
        self,
        session: TerminalSession,
        *,
        actor_ref: str,
        operation: OperationContext,
        action: AuthorizationAction,
        side_effect: str,
        payload: JsonValue,
        approval_id: str | None = None,
        risk: RiskClassification,
    ) -> None:
        await self._enforce(
            actor_ref=actor_ref,
            operation=replace(operation, project_id=session.context.project_id),
            action=action,
            resource_id=session.id,
            workspace_id=session.context.workspace_id,
            task_id=session.context.task_id,
            run_id=session.context.run_id,
            node_id=session.context.node_id,
            side_effect=side_effect,
            security_labels=("terminal_session", *session.policy_classification),
            payload=payload,
            approval_id=approval_id,
            risk=risk,
        )

    async def _enforce(
        self,
        *,
        actor_ref: str,
        operation: OperationContext,
        action: AuthorizationAction,
        resource_id: str,
        workspace_id: str | None,
        task_id: str | None = None,
        run_id: str | None = None,
        node_id: str | None = None,
        side_effect: str,
        security_labels: tuple[str, ...],
        payload: JsonValue,
        approval_id: str | None = None,
        risk: RiskClassification,
    ) -> None:
        actor = infer_actor_identity(actor_ref)
        proposed = ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=action,
                resource_type=ResourceType.GENERIC,
                resource_id=resource_id,
                operation=operation,
                workspace_id=workspace_id,
                task_id=task_id,
                run_id=run_id,
                node_id=node_id,
                side_effect=side_effect,
                security_labels=security_labels,
            ),
            payload=payload,
        )
        await self._authorization.enforce(proposed, approval_id=approval_id, risk=risk)

    def _canonical_frame(self, session_id: str, frame: AdapterFrame) -> TerminalFrame:
        existing = self._frames[session_id].get(frame.sequence)
        redacted = self._redact(frame.data)
        if existing is not None:
            if (
                existing.channel is not frame.channel
                or existing.data != redacted
                or existing.final != frame.final
            ):
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "terminal adapter changed an already-observed stream sequence",
                )
            return existing
        canonical = TerminalFrame(
            session_id=session_id,
            sequence=frame.sequence,
            channel=frame.channel,
            data=redacted,
            final=frame.final,
        )
        self._frames[session_id][frame.sequence] = canonical
        return canonical

    def _record(
        self,
        session_id: str,
        actor_ref: str,
        action: str,
        operation: OperationContext,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        self._activity.append(
            TerminalActivityRecord(
                session_id=session_id,
                actor_ref=actor_ref,
                action=action,
                correlation_id=operation.correlation_id,
                metadata=metadata or {},
            )
        )


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
