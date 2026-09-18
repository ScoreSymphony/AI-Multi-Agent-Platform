"""Control Plane diagnostics and operator transitions for external-effect recovery."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import CommandHandler, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .recovery import (
    ExternalEffectRecoveryCoordinator,
    external_effect_recovery_resource,
)

EXTERNAL_EFFECT_RECOVERY_COLLECTION = "external-effect-recoveries"
EXTERNAL_EFFECT_RECONCILE_COMMAND = "external-effect.reconcile"
EXTERNAL_EFFECT_AUTHORIZE_RETRY_COMMAND = "external-effect.authorize-retry"
EXTERNAL_EFFECT_MARK_FAILED_COMMAND = "external-effect.mark-failed"
EXTERNAL_EFFECT_CONFIRM_SUCCEEDED_COMMAND = "external-effect.confirm-succeeded"


class ExternalEffectRecoveryControlPlane(Protocol):
    def register_resource_service(self, collection: str, service: ResourceService) -> None: ...
    def register_command(self, command: str, handler: CommandHandler) -> None: ...


class ExternalEffectRecoveryResourceService:
    def __init__(self, recovery: ExternalEffectRecoveryCoordinator) -> None:
        self.recovery = recovery

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            external_effect_recovery_resource(record)
            for record in self.recovery.list_records()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return external_effect_recovery_resource(self.recovery.get_record(resource_id))


class ExternalEffectRecoveryCommands:
    def __init__(self, recovery: ExternalEffectRecoveryCoordinator) -> None:
        self.recovery = recovery

    async def reconcile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context, payload
        return external_effect_recovery_resource(
            await self.recovery.reconcile_effect(resource_ref)
        )

    async def authorize_retry(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return external_effect_recovery_resource(
            await self.recovery.authorize_retry(
                resource_ref,
                actor=context.actor.principal_ref,
                reason=_required_reason(payload),
            )
        )

    async def mark_failed(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return external_effect_recovery_resource(
            await self.recovery.mark_failed(
                resource_ref,
                actor=context.actor.principal_ref,
                reason=_required_reason(payload),
            )
        )

    async def confirm_succeeded(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        result_ref = payload.get("result_ref")
        if result_ref is not None and (
            not isinstance(result_ref, str) or not result_ref.strip()
        ):
            raise ContractError(ErrorCode.INVALID_REQUEST, "result_ref must be a non-blank string")
        raw_evidence = payload.get("evidence_refs", [])
        if not isinstance(raw_evidence, list):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "evidence_refs must contain non-blank strings",
            )
        evidence_refs: list[str] = []
        for item in raw_evidence:
            if not isinstance(item, str) or not item.strip():
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "evidence_refs must contain non-blank strings",
                )
            evidence_refs.append(item)
        return external_effect_recovery_resource(
            await self.recovery.confirm_succeeded(
                resource_ref,
                actor=context.actor.principal_ref,
                reason=_required_reason(payload),
                result_ref=result_ref,
                evidence_refs=tuple(evidence_refs),
            )
        )


def register_external_effect_recovery_control_plane(
    control_plane: ExternalEffectRecoveryControlPlane,
    recovery: ExternalEffectRecoveryCoordinator,
) -> None:
    """Expose only redacted diagnostics; retry execution stays on the normal capability path."""

    commands = ExternalEffectRecoveryCommands(recovery)
    control_plane.register_resource_service(
        EXTERNAL_EFFECT_RECOVERY_COLLECTION,
        ExternalEffectRecoveryResourceService(recovery),
    )
    control_plane.register_command(
        EXTERNAL_EFFECT_RECONCILE_COMMAND,
        commands.reconcile,
    )
    control_plane.register_command(
        EXTERNAL_EFFECT_AUTHORIZE_RETRY_COMMAND,
        commands.authorize_retry,
    )
    control_plane.register_command(
        EXTERNAL_EFFECT_MARK_FAILED_COMMAND,
        commands.mark_failed,
    )
    control_plane.register_command(
        EXTERNAL_EFFECT_CONFIRM_SUCCEEDED_COMMAND,
        commands.confirm_succeeded,
    )


def _required_reason(payload: dict[str, JsonValue]) -> str:
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "operator recovery action requires a non-blank reason",
        )
    return reason.strip()
