"""Canonical identity, action/resource vocabulary and local authorization provider."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    AuthorizationProvider,
    HealthStatus,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import AuthorizationRequest as BaseAuthorizationRequest


class ActorType(StrEnum):
    HUMAN = "human"
    SERVICE = "service"
    AGENT = "agent"
    WORKER = "worker"
    AUTOMATION = "automation"
    INTEGRATION = "integration"


class AuthorizationAction(StrEnum):
    VIEW = "view"
    READ = "read"
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    EXECUTE = "execute"
    APPROVE = "approve"
    ADMINISTER = "administer"
    MANAGE_INTEGRATIONS = "manage_integrations"
    MANAGE_CREDENTIALS = "manage_credentials"
    DISPATCH = "dispatch"
    INVOKE_SENSITIVE_CAPABILITY = "invoke_sensitive_capability"


class ResourceType(StrEnum):
    ORGANIZATION = "organization"
    TEAM = "team"
    PROJECT = "project"
    WORKSPACE = "workspace"
    TASK = "task"
    RUN = "run"
    AGENT = "agent"
    AGENT_TEAM = "agent_team"
    CAPABILITY = "capability"
    TOOL = "tool"
    FILE = "file"
    ARTIFACT = "artifact"
    MEMORY = "memory"
    KNOWLEDGE_SOURCE = "knowledge_source"
    MODEL_CONFIGURATION = "model_configuration"
    PROVIDER_CONFIGURATION = "provider_configuration"
    NODE = "node"
    WORKER = "worker"
    AUTOMATION = "automation"
    CONNECTOR = "connector"
    INTEGRATION = "integration"
    SECRET_REFERENCE = "secret_reference"
    PLUGIN = "plugin"
    ADMINISTRATIVE_SETTINGS = "administrative_settings"
    GENERIC = "generic"


class RiskClassification(StrEnum):
    STANDARD = "standard"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ActorIdentity:
    actor_id: str
    actor_type: ActorType
    organization_id: str | None = None
    team_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("actor_id must not be blank")
        if self.organization_id is not None and not self.organization_id.strip():
            raise ValueError("organization_id must not be blank when provided")
        if any(not team_id.strip() for team_id in self.team_ids):
            raise ValueError("team_ids must not contain blank values")
        object.__setattr__(self, "team_ids", tuple(self.team_ids))


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    actor: ActorIdentity
    action: AuthorizationAction
    resource_type: ResourceType
    resource_id: str
    operation: OperationContext
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

    def __post_init__(self) -> None:
        if not self.resource_id.strip():
            raise ValueError("resource_id must not be blank")
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
        ):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")
        if any(not label.strip() for label in self.security_labels):
            raise ValueError("security_labels must not contain blank values")
        object.__setattr__(self, "security_labels", tuple(self.security_labels))
        object.__setattr__(self, "trust_context", MappingProxyType(dict(self.trust_context)))

    def to_request(
        self,
        *,
        requested_action_digest: str | None = None,
        approval_id: str | None = None,
    ) -> AuthorizationRequest:
        from ai_multi_agent_platform.contracts import AuthorizationRequest

        return AuthorizationRequest(
            principal_ref=self.actor.actor_id,
            actor_type=self.actor.actor_type.value,
            action=self.action.value,
            resource_type=self.resource_type.value,
            resource_ref=self.resource_id,
            context=self.operation,
            organization_id=self.organization_id or self.actor.organization_id,
            team_id=self.team_id,
            workspace_id=self.workspace_id,
            task_id=self.task_id,
            run_id=self.run_id,
            agent_id=self.agent_id,
            capability_ref=self.capability_ref,
            side_effect=self.side_effect,
            security_labels=self.security_labels,
            node_id=self.node_id,
            trust_context=self.trust_context,
            requested_action_digest=requested_action_digest,
            approval_id=approval_id,
        )


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """Exact action proposed for authorization/approval.

    Payload content is never persisted in approval/audit records. Only the digest and
    an optional safe payload reference are retained.
    """

    context: AuthorizationContext
    payload: JsonValue = None
    payload_ref: str | None = None

    def __post_init__(self) -> None:
        if self.payload_ref is not None and not self.payload_ref.strip():
            raise ValueError("payload_ref must not be blank when provided")

    @property
    def digest(self) -> str:
        payload = {
            "actor_id": self.context.actor.actor_id,
            "actor_type": self.context.actor.actor_type.value,
            "action": self.context.action.value,
            "resource_type": self.context.resource_type.value,
            "resource_id": self.context.resource_id,
            "project_id": self.context.operation.project_id,
            "organization_id": self.context.organization_id or self.context.actor.organization_id,
            "team_id": self.context.team_id,
            "workspace_id": self.context.workspace_id,
            "task_id": self.context.task_id,
            "run_id": self.context.run_id,
            "agent_id": self.context.agent_id,
            "capability_ref": self.context.capability_ref,
            "side_effect": self.context.side_effect,
            "security_labels": sorted(self.context.security_labels),
            "node_id": self.context.node_id,
            "trust_context": dict(self.context.trust_context),
            "payload_ref": self.payload_ref,
            "payload": self.payload,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthorizationAuditRecord:
    actor_ref: str
    actor_type: ActorType
    action: AuthorizationAction
    resource_type: ResourceType
    resource_id: str
    outcome: AuthorizationOutcome
    reason: str | None
    policy_id: str | None
    occurred_at: datetime
    correlation_id: str
    project_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    approval_id: str | None = None
    requested_action_digest: str | None = None


@dataclass(frozen=True, slots=True)
class LocalPrincipalPolicy:
    """Deterministic self-hosted policy entry.

    Empty scope/resource sets mean "not additionally restricted". Actions remain
    deny-by-default unless explicitly allowed, approval-gated, or administrator-owned.
    """

    principal_ref: str
    actor_types: frozenset[ActorType]
    allowed_actions: frozenset[AuthorizationAction] = frozenset()
    approval_actions: frozenset[AuthorizationAction] = frozenset()
    resource_types: frozenset[ResourceType] = frozenset()
    project_ids: frozenset[str] = frozenset()
    organization_ids: frozenset[str] = frozenset()
    workspace_ids: frozenset[str] = frozenset()
    administrator: bool = False

    def __post_init__(self) -> None:
        if not self.principal_ref.strip():
            raise ValueError("principal_ref must not be blank")
        if not self.actor_types:
            raise ValueError("local policy requires at least one actor type")
        if not (self.allowed_actions or self.approval_actions or self.administrator):
            raise ValueError("local policy must grant, gate, or administer at least one action")


class LocalAuthorizationProvider(AuthorizationProvider):
    """Dependency-free reference authorization provider with deterministic decisions."""

    def __init__(
        self,
        policies: tuple[LocalPrincipalPolicy, ...] = (),
        *,
        provider_id: str = "local-authorization",
    ) -> None:
        if not provider_id.strip():
            raise ValueError("provider_id must not be blank")
        self._provider_id = provider_id
        self._policies: dict[str, LocalPrincipalPolicy] = {}
        for policy in policies:
            self.register(policy)

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="authorization",
            supported_operations=("authorize",),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    def register(self, policy: LocalPrincipalPolicy) -> None:
        if policy.principal_ref in self._policies:
            raise ValueError(f"duplicate local authorization policy for {policy.principal_ref!r}")
        self._policies[policy.principal_ref] = policy

    async def authorize(self, request: BaseAuthorizationRequest) -> AuthorizationDecision:
        policy = self._policies.get(request.principal_ref)
        if policy is None:
            return self._decision(
                AuthorizationOutcome.DENY,
                request,
                reason="principal has no local authorization policy",
                policy_id="local:deny-unregistered",
            )

        try:
            actor_type = ActorType(getattr(request, "actor_type", "service"))
            action = AuthorizationAction(request.action)
            resource_type = ResourceType(getattr(request, "resource_type", "generic"))
        except ValueError:
            return self._decision(
                AuthorizationOutcome.DENY,
                request,
                reason="authorization vocabulary is not recognized by local policy",
                policy_id=f"local:{policy.principal_ref}",
            )

        if actor_type not in policy.actor_types:
            return self._decision(
                AuthorizationOutcome.DENY,
                request,
                reason="actor type does not match principal policy",
                policy_id=f"local:{policy.principal_ref}",
            )
        if policy.resource_types and resource_type not in policy.resource_types:
            return self._decision(
                AuthorizationOutcome.DENY,
                request,
                reason="resource type is outside principal policy",
                policy_id=f"local:{policy.principal_ref}",
            )
        if not _scope_allowed(request.context.project_id, policy.project_ids):
            return self._decision(
                AuthorizationOutcome.DENY,
                request,
                reason="project scope is outside principal policy",
                policy_id=f"local:{policy.principal_ref}",
            )
        if not _scope_allowed(getattr(request, "organization_id", None), policy.organization_ids):
            return self._decision(
                AuthorizationOutcome.DENY,
                request,
                reason="organization scope is outside principal policy",
                policy_id=f"local:{policy.principal_ref}",
            )
        if not _scope_allowed(getattr(request, "workspace_id", None), policy.workspace_ids):
            return self._decision(
                AuthorizationOutcome.DENY,
                request,
                reason="workspace scope is outside principal policy",
                policy_id=f"local:{policy.principal_ref}",
            )

        policy_id = f"local:{policy.principal_ref}"
        if action in policy.approval_actions:
            return self._decision(
                AuthorizationOutcome.REQUIRE_APPROVAL,
                request,
                reason="action requires approval by local policy",
                policy_id=policy_id,
            )
        if policy.administrator or action in policy.allowed_actions:
            return self._decision(
                AuthorizationOutcome.ALLOW,
                request,
                reason="action allowed by local policy",
                policy_id=policy_id,
            )
        return self._decision(
            AuthorizationOutcome.DENY,
            request,
            reason="action is not granted by local policy",
            policy_id=policy_id,
        )

    @staticmethod
    def _decision(
        outcome: AuthorizationOutcome,
        request: BaseAuthorizationRequest,
        *,
        reason: str,
        policy_id: str,
    ) -> AuthorizationDecision:
        return AuthorizationDecision(
            outcome,
            reason=reason,
            policy_id=policy_id,
            audit_metadata={
                "actor_type": getattr(request, "actor_type", "service"),
                "resource_type": getattr(request, "resource_type", "generic"),
                "project_id": request.context.project_id,
            },
        )


def infer_actor_identity(actor_ref: str, *, organization_id: str | None = None) -> ActorIdentity:
    """Infer only stable platform actor classes from canonical/reference prefixes."""

    if not actor_ref.strip():
        raise ValueError("actor_ref must not be blank")
    if actor_ref.startswith("agent_") or actor_ref.startswith("agent:"):
        actor_type = ActorType.AGENT
    elif actor_ref.startswith("worker_") or actor_ref.startswith("worker:"):
        actor_type = ActorType.WORKER
    elif actor_ref.startswith("user_") or actor_ref.startswith("user:"):
        actor_type = ActorType.HUMAN
    elif actor_ref.startswith("automation_") or actor_ref.startswith("automation:"):
        actor_type = ActorType.AUTOMATION
    elif actor_ref.startswith("integration_") or actor_ref.startswith("integration:"):
        actor_type = ActorType.INTEGRATION
    else:
        actor_type = ActorType.SERVICE
    return ActorIdentity(actor_ref, actor_type, organization_id=organization_id)


def _scope_allowed(value: str | None, allowed: frozenset[str]) -> bool:
    if not allowed:
        return True
    return value is not None and value in allowed
