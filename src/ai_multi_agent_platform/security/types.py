"""Platform-owned security context and baseline decision types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue


class SecurityDecision(StrEnum):
    """Portable security decision used before the final authorization engine exists."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ExternalSideEffect(StrEnum):
    """Security classification for operations that may affect state outside the platform."""

    NONE = "none"
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class SecurityContext:
    """Canonical actor/action/resource context for security-critical decisions and audit."""

    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    project_id: str | None = None
    workspace_id: str | None = None
    correlation_id: str | None = None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        for name in ("actor_id", "action", "resource_type", "resource_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        for name in ("project_id", "workspace_id", "correlation_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")


@dataclass(frozen=True, slots=True)
class SecretReference:
    """A scoped reference to secret material; the plaintext value is intentionally absent."""

    provider: str
    secret_id: str
    scope: str
    version: str | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("provider", "secret_id", "scope"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.version is not None and not self.version.strip():
            raise ValueError("version must not be blank when provided")

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical non-secret serialization form."""

        payload: dict[str, JsonValue] = {
            "provider": self.provider,
            "secret_id": self.secret_id,
            "scope": self.scope,
            "metadata": dict(self.metadata),
        }
        if self.version is not None:
            payload["version"] = self.version
        return payload


@dataclass(frozen=True, slots=True)
class SecurityAuditEvent:
    """Minimal reusable shape for security-sensitive audit records."""

    event_type: str
    context: SecurityContext
    decision: SecurityDecision
    reason: str
    side_effect: ExternalSideEffect = ExternalSideEffect.NONE
    details: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ValueError("event_type must not be blank")
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
