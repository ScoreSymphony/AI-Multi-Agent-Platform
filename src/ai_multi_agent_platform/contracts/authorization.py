"""Canonical, provider-neutral authorization request and decision contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from .types import AdapterMetadata, JsonValue
from .types import AuthorizationDecision as LegacyAuthorizationDecision
from .types import AuthorizationRequest as LegacyAuthorizationRequest


class AuthorizationOutcome(StrEnum):
    """Stable result vocabulary for every authorization provider."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True, slots=True)
class AuthorizationRequest(LegacyAuthorizationRequest):
    """Richer issue-#15 request while remaining an issue-#5 request subtype."""

    actor_type: str = "service"
    resource_type: str = "generic"
    organization_id: str | None = None
    team_id: str | None = None
    workspace_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    capability_ref: str | None = None
    side_effect: str | None = None
    security_labels: tuple[str, ...] = ()
    node_id: str | None = None
    trust_context: Mapping[str, JsonValue] = field(default_factory=dict)
    request_payload_digest: str | None = None
    requested_action_digest: str | None = None
    approval_id: str | None = None

    def __post_init__(self) -> None:
        LegacyAuthorizationRequest.__post_init__(self)
        for name in ("actor_type", "resource_type"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        for name in (
            "organization_id",
            "team_id",
            "workspace_id",
            "task_id",
            "run_id",
            "agent_id",
            "capability_ref",
            "side_effect",
            "node_id",
            "request_payload_digest",
            "requested_action_digest",
            "approval_id",
        ):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")
        if any(not label.strip() for label in self.security_labels):
            raise ValueError("security_labels must not contain blank values")
        object.__setattr__(self, "security_labels", tuple(self.security_labels))
        object.__setattr__(self, "trust_context", MappingProxyType(dict(self.trust_context)))


@dataclass(frozen=True, slots=True, init=False)
class AuthorizationDecision(LegacyAuthorizationDecision):
    """Portable tri-state decision compatible with the original boolean contract."""

    outcome: AuthorizationOutcome = AuthorizationOutcome.DENY
    policy_id: str | None = None
    constraints: Mapping[str, JsonValue] = field(default_factory=dict)
    audit_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __init__(
        self,
        outcome: AuthorizationOutcome | str | bool | None = None,
        *,
        allowed: bool | None = None,
        require_approval: bool = False,
        reason: str | None = None,
        policy_id: str | None = None,
        constraints: Mapping[str, JsonValue] | None = None,
        audit_metadata: Mapping[str, JsonValue] | None = None,
        adapter_metadata: tuple[AdapterMetadata, ...] = (),
    ) -> None:
        if outcome is not None and allowed is not None:
            raise ValueError("specify outcome or allowed, not both")
        if require_approval and (outcome is not None or allowed is not None):
            raise ValueError("require_approval cannot be combined with outcome/allowed")
        if require_approval:
            resolved = AuthorizationOutcome.REQUIRE_APPROVAL
        elif allowed is not None:
            resolved = AuthorizationOutcome.ALLOW if allowed else AuthorizationOutcome.DENY
        elif isinstance(outcome, bool):
            resolved = AuthorizationOutcome.ALLOW if outcome else AuthorizationOutcome.DENY
        elif outcome is None:
            raise ValueError("authorization decision requires an outcome")
        else:
            resolved = AuthorizationOutcome(outcome)

        if reason is not None and not reason.strip():
            raise ValueError("reason must not be blank when provided")
        if policy_id is not None and not policy_id.strip():
            raise ValueError("policy_id must not be blank when provided")

        object.__setattr__(self, "allowed", resolved is AuthorizationOutcome.ALLOW)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "adapter_metadata", tuple(adapter_metadata))
        object.__setattr__(self, "outcome", resolved)
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "constraints", MappingProxyType(dict(constraints or {})))
        object.__setattr__(self, "audit_metadata", MappingProxyType(dict(audit_metadata or {})))

    @property
    def requires_approval(self) -> bool:
        return self.outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def normalize_authorization_decision(
    decision: LegacyAuthorizationDecision,
) -> AuthorizationDecision:
    """Upgrade an issue-#5 boolean decision at the issue-#15 boundary."""

    if isinstance(decision, AuthorizationDecision):
        return decision
    return AuthorizationDecision(
        allowed=decision.allowed,
        reason=decision.reason,
        adapter_metadata=decision.adapter_metadata,
    )
