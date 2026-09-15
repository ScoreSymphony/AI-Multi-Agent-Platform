"""Security construction for the supported single-node composition."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.observability import ObservedAuthorizationProvider
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizedSecretProvider,
    ControlPlaneAuthorizationBridge,
    LocalAuthenticationService,
    LocalPrincipalPolicy,
    ResourceType,
    SqliteApprovalService,
    SqliteAuthorizationAuditSink,
)
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider

from ..config import SingleNodeConfig
from .observability import ObservabilityBundle

_EVALUATION_OWNER_ID = "evaluation-single-node"
_EVALUATION_PRINCIPAL = f"service:{_EVALUATION_OWNER_ID}"
_PLATFORM_SERVICE_PRINCIPAL = "service:platform"


@dataclass(frozen=True, slots=True)
class SecurityBundle:
    """Authentication, authorization and approval authorities for dependent layers."""

    authentication: LocalAuthenticationService
    authorization: SqliteLocalAuthorizationProvider
    authorization_audit: SqliteAuthorizationAuditSink
    approval_gate: AuthorizationGate
    control_plane_authorization: ControlPlaneAuthorizationBridge
    secrets: SecretProvider | None


def build_security(
    config: SingleNodeConfig,
    observability: ObservabilityBundle,
    *,
    secret_provider: SecretProvider | None = None,
) -> SecurityBundle:
    """Build durable local security state and the canonical approval gate."""

    database_dir = config.database_dir
    authentication_store = SqliteAuthenticationStore(database_dir / "authentication.sqlite3")
    authentication = LocalAuthenticationService(store=authentication_store)
    authorization = SqliteLocalAuthorizationProvider(database_dir / "authorization.sqlite3")

    if not authorization.has_policy(_EVALUATION_PRINCIPAL):
        authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_EVALUATION_PRINCIPAL,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.EXECUTE,
                        AuthorizationAction.READ,
                        AuthorizationAction.VIEW,
                        AuthorizationAction.MODIFY,
                    }
                ),
                resource_types=frozenset(
                    {
                        ResourceType.RUN,
                        ResourceType.TASK,
                        ResourceType.AGENT,
                        ResourceType.FILE,
                        ResourceType.MEMORY,
                    }
                ),
            )
        )
    if not authorization.has_policy(_PLATFORM_SERVICE_PRINCIPAL):
        authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_PLATFORM_SERVICE_PRINCIPAL,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.READ,
                        AuthorizationAction.MANAGE_CREDENTIALS,
                    }
                ),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
            )
        )

    observed_authorization = ObservedAuthorizationProvider(
        authorization,
        observability.telemetry,
    )
    approval_service = SqliteApprovalService(database_dir / "approvals.sqlite3")
    authorization_audit = SqliteAuthorizationAuditSink(database_dir / "authorization-audit.sqlite3")
    approval_gate = AuthorizationGate(
        observed_authorization,
        approvals=approval_service,
        audit_sink=authorization_audit,
    )
    control_plane_authorization = ControlPlaneAuthorizationBridge(approval_gate)
    protected_secret_provider: SecretProvider | None = (
        AuthorizedSecretProvider(secret_provider, approval_gate)
        if secret_provider is not None
        else None
    )

    return SecurityBundle(
        authentication=authentication,
        authorization=authorization,
        authorization_audit=authorization_audit,
        approval_gate=approval_gate,
        control_plane_authorization=control_plane_authorization,
        secrets=protected_secret_provider,
    )
