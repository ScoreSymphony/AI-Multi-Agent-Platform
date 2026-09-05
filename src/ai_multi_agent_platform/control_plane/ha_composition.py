"""Optional #89 Control Plane composition with active/passive authority gates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.high_availability.contracts import CoordinationError
from ai_multi_agent_platform.high_availability.integrations import AuthorityGatedAutomationLoop
from ai_multi_agent_platform.high_availability.service import ControlPlaneFailoverService

from .approval_portability_composition import ControlPlane as _BaseControlPlane
from .models import RequestContext

_READ_ONLY_ACTION_SUFFIXES = (
    ":list",
    ":read",
    ":subscribe",
    ":health",
    ".list",
    ".read",
    ".subscribe",
    ".health",
)


class ControlPlane(_BaseControlPlane):
    """Current Control Plane plus optional #89 leadership/readiness enforcement."""

    def __init__(
        self,
        *args: Any,
        failover: ControlPlaneFailoverService,
        ha_automation_poll_seconds: float = 1.0,
        **kwargs: Any,
    ) -> None:
        self._failover = failover
        super().__init__(*args, **kwargs)
        self._ha_automation_runtime = AuthorityGatedAutomationLoop(
            self.automation_runtime,
            self._require_ha_authority,
            poll_interval_seconds=ha_automation_poll_seconds,
        )

    @property
    def failover(self) -> ControlPlaneFailoverService:
        return self._failover

    async def health(self) -> dict[str, JsonValue]:
        health = await super().health()
        status = await self._failover.status()
        ha_ready = status.role.value == "active" and status.coordination_available
        health["ready"] = health.get("ready") is True and ha_ready
        health["high_availability"] = {
            "instance_id": status.instance_id,
            "mode": status.mode.value,
            "role": status.role.value,
            "leader_instance_id": status.leader_instance_id,
            "epoch": status.epoch,
            "lease_expires_at": (
                status.lease_expires_at.isoformat()
                if status.lease_expires_at is not None
                else None
            ),
            "coordination_available": status.coordination_available,
            "promotion_count": status.promotion_count,
            "last_promotion_reason": status.last_promotion_reason,
        }
        return health

    async def start_automation_runtime(self) -> None:
        await self._ha_automation_runtime.start()

    async def stop_automation_runtime(self) -> None:
        await self._ha_automation_runtime.stop()

    async def run_automation_runtime_once(self) -> Any:
        return await self._ha_automation_runtime.run_once()

    async def _authorize(
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
        await super()._authorize(
            context,
            action,
            resource_ref,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )
        if self._authority_required(context, action):
            await self._require_ha_authority()

    def _authority_required(self, context: RequestContext, action: str) -> bool:
        registered_commands = getattr(self, "registered_commands", ())
        if action in registered_commands:
            return True
        if action.endswith(_READ_ONLY_ACTION_SUFFIXES):
            return False
        return context.idempotency_key is not None

    async def _require_ha_authority(self) -> Any:
        try:
            return await self._failover.require_authority()
        except CoordinationError as exc:
            status = await self._failover.status()
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Control Plane instance is not authoritative for this operation",
                retryable=True,
                details={
                    "ha_role": status.role.value,
                    "ha_epoch": status.epoch,
                    "ha_instance_id": status.instance_id,
                },
            ) from exc


__all__ = ["ControlPlane"]
