"""Operator-facing Worker credential lifecycle for advanced #240 deployments.

The runtime Control Plane already owns authenticated administration. This adapter binds
credential issuance to the selected deployment profile so an administrator can provision or
rotate the reporter identity required by ``platform-worker`` without test-only Python calls.
Canonical Worker authentication and authorization remain owned by their security services.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    CredentialScope,
    LocalAuthenticationService,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.security.async_authorization_policy import (
    AsyncAuthorizationPolicyService,
    runtime_authorization_policy_service,
)
from ai_multi_agent_platform.security.async_worker_credentials import (
    AsyncWorkerCredentialService,
    runtime_worker_credential_service,
)
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider

from .advanced_profiles import AdvancedDeploymentProfile, DeploymentNode

_WORKER_PROTOCOL_ACTIONS = frozenset(
    {
        AuthorizationAction.CREATE,
        AuthorizationAction.MODIFY,
        AuthorizationAction.DELETE,
    }
)
_WORKER_PROTOCOL_RESOURCES = frozenset({ResourceType.NODE, ResourceType.WORKER})


class DistributedWorkerAdmin:
    """Provision profile-bound reporter credentials through the authenticated Control Plane."""

    def __init__(
        self,
        profile: AdvancedDeploymentProfile,
        authentication: LocalAuthenticationService,
        authorization: SqliteLocalAuthorizationProvider,
        *,
        runtime_credentials: AsyncWorkerCredentialService | None = None,
        runtime_authorization_policies: AsyncAuthorizationPolicyService | None = None,
    ) -> None:
        self._profile = profile
        self._authentication = authentication
        self._authorization = authorization
        self._runtime_credentials = runtime_worker_credential_service(
            authentication,
            runtime_service=runtime_credentials,
        )
        self._runtime_authorization_policies = runtime_authorization_policy_service(
            authorization,
            runtime_service=runtime_authorization_policies,
        )

    async def provision(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        node = self._reporter_node(resource_ref)
        purpose = _optional_string(payload, "purpose") or "distributed Worker authentication"
        _only_keys(payload, {"purpose"})
        await self._ensure_worker_policy(resource_ref)

        now = datetime.now(UTC)
        provisioning = await self._runtime_credentials.provision_worker_credential(
            resource_ref,
            purpose=purpose,
            scope=self._credential_scope(node),
            now=now,
        )
        if provisioning.issued is None:
            return {
                "id": resource_ref,
                "type": "worker-credential-provisioning",
                "state": "already_provisioned",
                "node_id": node.node.node_id,
                "active_credential_ids": list(provisioning.active_credential_ids),
                "secret_display": "not_recoverable",
            }

        issued = provisioning.issued
        return {
            "id": resource_ref,
            "type": "worker-credential-provisioning",
            "state": "provisioned",
            "node_id": node.node.node_id,
            "credential_id": issued.credential_id,
            "secret": issued.secret,
            "expires_at": issued.expires_at.isoformat() if issued.expires_at is not None else None,
            "secret_display": "one_time",
        }

    async def rotate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        node = self._reporter_node(resource_ref)
        _only_keys(payload, {"credential_id", "purpose"})
        credential_id = _required_string(payload, "credential_id")
        purpose = _optional_string(payload, "purpose")
        await self._ensure_worker_policy(resource_ref)
        try:
            rotation = await self._runtime_credentials.rotate_worker_credential(
                resource_ref,
                credential_id,
                purpose=purpose,
                scope=self._credential_scope(node),
            )
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Worker credential not found for reporter: {credential_id}",
            ) from exc
        replacement = rotation.replacement
        return {
            "id": resource_ref,
            "type": "worker-credential-rotation",
            "state": "rotated",
            "node_id": node.node.node_id,
            "previous_credential_id": rotation.previous_credential_id,
            "credential_id": replacement.credential_id,
            "secret": replacement.secret,
            "expires_at": (
                replacement.expires_at.isoformat() if replacement.expires_at is not None else None
            ),
            "secret_display": "one_time",
        }

    def _reporter_node(self, worker_id: str) -> DeploymentNode:
        for node in self._profile.nodes:
            if node.reporter_worker_id == worker_id:
                return node
        declared_worker = any(
            worker.worker_id == worker_id for node in self._profile.nodes for worker in node.workers
        )
        if declared_worker:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "only the profile reporter Worker receives a Worker-protocol credential",
            )
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"Worker is not declared by the selected deployment profile: {worker_id}",
        )

    def _credential_scope(self, node: DeploymentNode) -> CredentialScope:
        return CredentialScope(
            actions=_WORKER_PROTOCOL_ACTIONS,
            resource_types=_WORKER_PROTOCOL_RESOURCES,
            resource_ids=frozenset(
                {node.node.node_id, *(worker.worker_id for worker in node.workers)}
            ),
        )

    async def _ensure_worker_policy(self, reporter_id: str) -> None:
        await self._runtime_authorization_policies.ensure_registered(
            LocalPrincipalPolicy(
                principal_ref=reporter_id,
                actor_types=frozenset({ActorType.WORKER}),
                allowed_actions=_WORKER_PROTOCOL_ACTIONS,
                resource_types=_WORKER_PROTOCOL_RESOURCES,
            )
        )


def register_distributed_worker_admin(
    control_plane: ControlPlane,
    *,
    profile: AdvancedDeploymentProfile,
    authentication: LocalAuthenticationService,
    authorization: SqliteLocalAuthorizationProvider,
    runtime_credentials: AsyncWorkerCredentialService | None = None,
    runtime_authorization_policies: AsyncAuthorizationPolicyService | None = None,
) -> DistributedWorkerAdmin:
    """Register the profile-bound operator credential lifecycle on the current Control Plane."""

    admin = DistributedWorkerAdmin(
        profile,
        authentication,
        authorization,
        runtime_credentials=runtime_credentials,
        runtime_authorization_policies=runtime_authorization_policies,
    )
    control_plane.register_command("worker.provision", admin.provision)
    control_plane.register_command("worker.rotate-credential", admin.rotate)
    return admin


def _only_keys(payload: dict[str, JsonValue], allowed: set[str]) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unsupported Worker credential fields: {sorted(unknown)!r}")


def _required_string(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value.strip()


def _optional_string(payload: dict[str, JsonValue], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string when provided")
    return value.strip()
