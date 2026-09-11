"""Shared authorization boundary for Control Plane operations."""

from __future__ import annotations

import hashlib
import json

from ai_multi_agent_platform.contracts.authorization import AuthorizationRequest
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import AuthorizationProvider
from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    JsonValue,
    OperationContext,
    OperationControl,
)
from ai_multi_agent_platform.kernel import TaskState

from .models import RequestContext


class ControlPlaneAuthorization:
    """Build canonical authorization requests and enforce allow/deny decisions."""

    def __init__(self, provider: AuthorizationProvider | None) -> None:
        self._provider = provider

    async def authorize_for_task(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        task: TaskState,
    ) -> None:
        await self.authorize(
            context,
            action,
            resource_ref,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
        )

    async def allowed_for_task(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        task: TaskState,
    ) -> bool:
        return await self.allowed(
            context,
            action,
            resource_ref,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
        )

    async def authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> None:
        decision = await self.decision(
            context,
            action,
            resource_ref,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )
        if decision is not None and not decision.allowed:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                decision.reason or "operation is forbidden",
            )

    async def allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> bool:
        decision = await self.decision(
            context,
            action,
            resource_ref,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )
        return decision is None or decision.allowed

    async def decision(
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
        if self._provider is None:
            return None
        effective_owner_type = owner_type if owner_type is not None else context.actor.owner_type
        effective_owner_id = owner_id if owner_id is not None else context.actor.owner_id
        return await self._provider.authorize(
            AuthorizationRequest(
                principal_ref=context.actor.principal_ref,
                action=action,
                resource_ref=resource_ref,
                context=OperationContext(
                    correlation_id=context.correlation_id,
                    owner_type=effective_owner_type,
                    owner_id=effective_owner_id,
                    project_id=project_id,
                    control=OperationControl(idempotency_key=context.idempotency_key),
                ),
                request_payload_digest=request_payload_digest,
            )
        )

    @staticmethod
    def payload_digest(payload: dict[str, JsonValue]) -> str:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
