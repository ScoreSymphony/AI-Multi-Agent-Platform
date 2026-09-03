"""Current Control Plane composition with #36 authentication context propagated to #15."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.authorization import AuthorizationRequest
from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    OperationContext,
    OperationControl,
)

from .hardened_automation_api import ControlPlane as _CurrentControlPlane
from .models import RequestContext


class ControlPlane(_CurrentControlPlane):
    """Propagate authenticated actor metadata into the canonical #15 request.

    Authentication establishes identity and credential-local constraints.  This class
    transports that trusted context; the configured authorization provider remains the
    authority that decides whether an operation is allowed.
    """

    async def _authorization_decision(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> AuthorizationDecision | None:
        if self._authorization is None:
            return None
        effective_owner_type = owner_type if owner_type is not None else context.actor.owner_type
        effective_owner_id = owner_id if owner_id is not None else context.actor.owner_id
        return await self._authorization.authorize(
            AuthorizationRequest(
                principal_ref=context.actor.principal_ref,
                actor_type=context.actor.actor_type or "service",
                action=action,
                resource_ref=resource_ref,
                context=OperationContext(
                    correlation_id=context.correlation_id,
                    owner_type=effective_owner_type,
                    owner_id=effective_owner_id,
                    project_id=project_id,
                    control=OperationControl(idempotency_key=context.idempotency_key),
                ),
                trust_context=context.actor.trust_context,
                request_payload_digest=request_payload_digest,
            )
        )
