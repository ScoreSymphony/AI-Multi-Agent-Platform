"""Digest-only Control Plane inspection for canonical egress compatibility."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
    JsonValue,
    OperationContext,
)
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext

from .egress import EgressGate
from .egress_profiles import EgressProfileRepository

EGRESS_POLICY_EVALUATE_COMMAND = "egress-policy.evaluate"


class EgressPolicyInspectionHandler:
    """Explain one disclosure decision without execution-side effects.

    Inspection deliberately invokes only the configured policy port. It does not call
    ``EgressGate.evaluate``/``enforce`` and therefore cannot emit execution audit records,
    create pending Approvals or consume/reuse an Approval. The request contains only
    classification, canonical references and a caller-supplied payload digest; protected
    payload content is never accepted by this endpoint.
    """

    def __init__(
        self,
        repository: EgressProfileRepository,
        gate: EgressGate,
    ) -> None:
        self.repository = repository
        self.gate = gate

    async def evaluate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del resource_ref
        target_kind = _enum(payload, "target_kind", EgressTargetKind)
        target_id = _required_string(payload, "target_id")
        project_id = _optional_string(payload, "project_id")
        classification = _enum(payload, "classification", DataClassification)
        profile = self.repository.resolve(target_kind, target_id, project_id)

        target = (
            EgressTarget(kind=target_kind, target_id=target_id, profile=profile)
            if profile is not None
            else EgressTarget(
                kind=target_kind,
                target_id=target_id,
                posture=EgressTargetPosture.UNKNOWN,
            )
        )
        request = EgressRequest(
            request_id=_required_string(payload, "request_id"),
            target=target,
            context=OperationContext(
                correlation_id=context.correlation_id,
                causation_id=context.request_id,
                owner_type=context.actor.owner_type,
                owner_id=context.actor.owner_id,
                project_id=project_id,
            ),
            classification=classification,
            resource_type=_required_string(payload, "resource_type"),
            payload_digest=_required_string(payload, "payload_digest"),
            task_id=_optional_string(payload, "task_id"),
            run_id=_optional_string(payload, "run_id"),
            capability_id=_optional_string(payload, "capability_id"),
        )

        # Read-only preview: policy evaluation only. The Gate's audit/Approval machinery
        # is intentionally bypassed so an operator inspection cannot impersonate execution.
        decision = await self.gate.policy.evaluate(request)
        try:
            decision.validate_against(request)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "egress policy returned an invalid inspection decision",
                details={
                    "egress_request_id": request.request_id,
                    "target_kind": target_kind.value,
                    "target_id": target_id,
                },
            ) from exc

        return {
            "target_kind": target_kind.value,
            "target_id": target_id,
            "project_id": project_id,
            "classification": classification.value,
            "outcome": decision.outcome.value,
            "reason_code": decision.reason_code.value,
            "policy_version": decision.policy_version,
            "profile_ref": (
                profile.canonical_ref
                if profile is not None
                else decision.audit_metadata.get("profile_ref")
            ),
            "profile_trust": (
                profile.trust.value
                if profile is not None
                else decision.audit_metadata.get("profile_trust")
            ),
            "cost_class": (
                profile.cost_class.value
                if profile is not None
                else decision.audit_metadata.get("cost_class")
            ),
            "approval_ref": decision.approval_ref,
            "payload_digest": request.payload_digest,
        }


def register_egress_policy_inspection(
    control_plane: ControlPlane,
    repository: EgressProfileRepository,
    gate: EgressGate,
) -> None:
    handler = EgressPolicyInspectionHandler(repository, gate)
    control_plane.register_command(EGRESS_POLICY_EVALUATE_COMMAND, handler.evaluate)


def _required_string(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a non-blank string")
    return item


def _optional_string(value: Mapping[str, object], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be a non-blank string when provided",
        )
    return item


def _enum[T](value: Mapping[str, object], field: str, enum_type: type[T]) -> T:
    item = _required_string(value, field)
    try:
        return enum_type(item)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST, f"{field} contains an unknown value"
        ) from exc


__all__ = [
    "EGRESS_POLICY_EVALUATE_COMMAND",
    "EgressPolicyInspectionHandler",
    "register_egress_policy_inspection",
]
