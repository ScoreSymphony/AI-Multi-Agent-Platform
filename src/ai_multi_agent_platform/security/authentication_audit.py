"""Redacted authentication audit emission shared by focused auth services."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue

from .authentication_models import AuthenticationAuditRecord, AuthenticationAuditSink


class AuthenticationAuditEmitter(Protocol):
    def __call__(
        self,
        event: str,
        *,
        now: datetime,
        success: bool,
        actor_id: str | None = None,
        subject_id: str | None = None,
        credential_id: str | None = None,
        correlation_id: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> None: ...


def emit_authentication_audit(
    audit_sink: AuthenticationAuditSink | None,
    event: str,
    *,
    now: datetime,
    success: bool,
    actor_id: str | None = None,
    subject_id: str | None = None,
    credential_id: str | None = None,
    correlation_id: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> None:
    if audit_sink is None:
        return
    audit_sink(
        AuthenticationAuditRecord(
            event=event,
            occurred_at=now,
            success=success,
            actor_id=actor_id,
            subject_id=subject_id,
            credential_id=credential_id,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )
    )


__all__ = ["AuthenticationAuditEmitter", "emit_authentication_audit"]
