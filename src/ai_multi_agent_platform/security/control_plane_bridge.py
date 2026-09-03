"""Compatibility bridge from the v1 Control Plane to the canonical authorization gate."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationProvider,
    HealthStatus,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationRequest as CanonicalAuthorizationRequest,
)
from ai_multi_agent_platform.contracts.types import AuthorizationRequest as BaseAuthorizationRequest

from .authorization import (
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    infer_actor_identity,
)
from .enforcement import AuthorizationGate


class ControlPlaneAuthorizationBridge(AuthorizationProvider):
    """Translate legacy northbound actions into canonical authorization decisions."""

    def __init__(self, gate: AuthorizationGate) -> None:
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        inner = self._gate.provider.descriptor
        return ProviderDescriptor(
            provider_id=f"{inner.provider_id}:control-plane-gate",
            provider_type="authorization",
            supported_operations=("authorize",),
            health=inner.health,
            available=inner.available,
        )

    async def health(self) -> HealthStatus:
        return await self._gate.provider.health()

    async def authorize(self, request: BaseAuthorizationRequest) -> AuthorizationDecision:
        action, resource_type = canonical_control_plane_vocabulary(request.action)
        task_id = request.resource_ref if request.resource_ref.startswith("task_") else None
        run_id = request.resource_ref if request.resource_ref.startswith("run_") else None
        request_payload_digest = (
            request.request_payload_digest
            if isinstance(request, CanonicalAuthorizationRequest)
            else None
        )
        proposed_payload = {"northbound_action": request.action}
        if request_payload_digest is not None:
            proposed_payload["request_payload_sha256"] = request_payload_digest
        proposed = ProposedAction(
            AuthorizationContext(
                actor=infer_actor_identity(request.principal_ref),
                action=action,
                resource_type=resource_type,
                resource_id=request.resource_ref,
                operation=request.context,
                task_id=task_id,
                run_id=run_id,
                side_effect=request.action,
            ),
            payload=proposed_payload,
        )
        return await self._gate.decide(proposed)


def canonical_control_plane_vocabulary(action: str) -> tuple[AuthorizationAction, ResourceType]:
    resource_name, separator, verb = action.partition(":")
    if not separator:
        resource_name, verb = "generic", action

    action_map = {
        "list": AuthorizationAction.VIEW,
        "read": AuthorizationAction.READ,
        "create": AuthorizationAction.CREATE,
        "queue": AuthorizationAction.EXECUTE,
        "start": AuthorizationAction.EXECUTE,
        "retry": AuthorizationAction.EXECUTE,
        "cancel": AuthorizationAction.MODIFY,
        "subscribe": AuthorizationAction.READ,
        "enable": AuthorizationAction.ADMINISTER,
        "disable": AuthorizationAction.ADMINISTER,
        "refresh-health": AuthorizationAction.ADMINISTER,
    }
    resource_map = {
        "project": ResourceType.PROJECT,
        "workspace": ResourceType.WORKSPACE,
        "task": ResourceType.TASK,
        "run": ResourceType.RUN,
        "artifact": ResourceType.ARTIFACT,
        "artifacts": ResourceType.ARTIFACT,
        "model-provider": ResourceType.PROVIDER_CONFIGURATION,
        "model": ResourceType.MODEL_CONFIGURATION,
    }
    return (
        action_map.get(verb, AuthorizationAction.MODIFY),
        resource_map.get(resource_name, ResourceType.GENERIC),
    )
