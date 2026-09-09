"""Application-owned fail-closed egress policy orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ai_multi_agent_platform.contracts.classification import (
    DataClassification,
    classification_strength,
    require_monotonic_classification,
)
from ai_multi_agent_platform.contracts.egress import (
    EgressAuditEvent,
    EgressAuditEventType,
    EgressAuditSink,
    EgressCostClass,
    EgressDecision,
    EgressOutcome,
    EgressPolicyPort,
    EgressProfileTrust,
    EgressReasonCode,
    EgressRequest,
    EgressTargetPosture,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext

from .authorization import ActorIdentity

type EgressActorResolver = Callable[[OperationContext], ActorIdentity]


class EgressApprovalResolver(Protocol):
    async def resolve(
        self,
        request: EgressRequest,
        decision: EgressDecision,
        *,
        actor: ActorIdentity,
        approval_id: str | None = None,
    ) -> EgressDecision: ...


class CanonicalEgressPolicy(EgressPolicyPort):
    """Conservative baseline policy for known application trust boundaries.

    Legacy targets retain the v1 posture/classification behavior. Targets that expose a
    versioned ``EgressProfile`` additionally enforce profile trust and cost policy: an
    unverified external profile and unknown/paid external cost are blocked by default.
    Secret, secret-reference and regulated data remain non-exportable unless the exact
    external target explicitly opts into the class and sensitive disclosure.
    """

    version = "egress-policy/v2"

    def __init__(
        self,
        *,
        allow_paid_external: bool = False,
        allow_unknown_external_cost: bool = False,
    ) -> None:
        self.allow_paid_external = allow_paid_external
        self.allow_unknown_external_cost = allow_unknown_external_cost

    async def evaluate(self, request: EgressRequest) -> EgressDecision:
        classification = request.classification
        target = request.target
        posture = target.effective_posture
        profile = target.profile
        if classification is None:
            return self._deny(request, EgressReasonCode.CLASSIFICATION_REQUIRED)
        if posture is EgressTargetPosture.UNKNOWN:
            return self._deny(
                request,
                EgressReasonCode.UNKNOWN_TARGET_POSTURE,
                outcome=EgressOutcome.UNKNOWN_BLOCKED,
            )
        if (
            profile is not None
            and posture is EgressTargetPosture.EXTERNAL
            and profile.trust is EgressProfileTrust.UNVERIFIED
        ):
            return self._deny(
                request,
                EgressReasonCode.UNVERIFIED_PROFILE,
                outcome=EgressOutcome.UNKNOWN_BLOCKED,
            )
        if classification in target.effective_denied_classifications:
            return self._deny(request, EgressReasonCode.TARGET_POLICY_DENIED)
        allowed_classifications = target.effective_allowed_classifications
        if allowed_classifications and classification not in allowed_classifications:
            return self._deny(request, EgressReasonCode.TARGET_POLICY_DENIED)
        if profile is not None and posture is EgressTargetPosture.EXTERNAL:
            if profile.cost_class is EgressCostClass.PAID_EXTERNAL and not self.allow_paid_external:
                return self._deny(request, EgressReasonCode.PAID_EXTERNAL_DENIED)
            if (
                profile.cost_class is EgressCostClass.UNKNOWN
                and not self.allow_unknown_external_cost
            ):
                return self._deny(
                    request,
                    EgressReasonCode.UNKNOWN_COST_BLOCKED,
                    outcome=EgressOutcome.UNKNOWN_BLOCKED,
                )
        if posture is EgressTargetPosture.EXTERNAL and classification in {
            DataClassification.SECRET,
            DataClassification.SECRET_REFERENCE,
            DataClassification.REGULATED,
        }:
            profile_opt_in = (
                profile is not None and profile.metadata.get("allow_sensitive_external") is True
            )
            legacy_opt_in = target.policy_metadata.get("allow_sensitive_external") is True
            explicitly_allowed = classification in allowed_classifications and (
                profile_opt_in or legacy_opt_in
            )
            if not explicitly_allowed:
                return self._deny(request, EgressReasonCode.SENSITIVE_EXTERNAL_DENIED)
        return EgressDecision(
            request_id=request.request_id,
            outcome=EgressOutcome.ALLOW,
            target_kind=target.kind,
            target_id=target.target_id,
            effective_classification=classification,
            reason_code=EgressReasonCode.ALLOWED,
            policy_version=self.version,
            audit_metadata=self._audit_metadata(request),
        )

    def _deny(
        self,
        request: EgressRequest,
        reason: EgressReasonCode,
        *,
        outcome: EgressOutcome = EgressOutcome.DENY,
    ) -> EgressDecision:
        return EgressDecision(
            request_id=request.request_id,
            outcome=outcome,
            target_kind=request.target.kind,
            target_id=request.target.target_id,
            effective_classification=request.classification,
            reason_code=reason,
            policy_version=self.version,
            audit_metadata=self._audit_metadata(request),
        )

    @staticmethod
    def _audit_metadata(request: EgressRequest) -> dict[str, JsonValue]:
        target = request.target
        metadata: dict[str, JsonValue] = {
            "target_posture": target.effective_posture.value,
        }
        if target.profile is not None:
            metadata.update(
                {
                    "profile_ref": target.profile.canonical_ref,
                    "profile_trust": target.profile.trust.value,
                    "cost_class": target.profile.cost_class.value,
                    "network_egress_required": target.profile.network_egress_required,
                    "policy_source": target.profile.policy_source,
                    "source_revision": target.profile.source_revision,
                }
            )
        return metadata


class NullEgressAuditSink(EgressAuditSink):
    async def record(self, event: EgressAuditEvent) -> None:
        del event


class InMemoryEgressAuditSink(EgressAuditSink):
    """Deterministic value-free audit collector for tests and local embeddings."""

    def __init__(self) -> None:
        self.events: list[EgressAuditEvent] = []

    async def record(self, event: EgressAuditEvent) -> None:
        self.events.append(event)


class EgressGate:
    """Single enforcement point used before every configured outbound boundary."""

    def __init__(
        self,
        policy: EgressPolicyPort | None = None,
        *,
        audit_sink: EgressAuditSink | None = None,
        approval_resolver: EgressApprovalResolver | None = None,
        actor_resolver: EgressActorResolver | None = None,
    ) -> None:
        self.policy = policy or CanonicalEgressPolicy()
        self.audit_sink = audit_sink or NullEgressAuditSink()
        self.approval_resolver = approval_resolver
        self.actor_resolver = actor_resolver

    async def evaluate(
        self,
        request: EgressRequest,
        *,
        actor: ActorIdentity | None = None,
        approval_id: str | None = None,
    ) -> EgressDecision:
        decision = await self.policy.evaluate(request)
        self._validate_decision(request, decision)

        effective_actor = actor
        if effective_actor is None and self.actor_resolver is not None:
            effective_actor = self.actor_resolver(request.context)
        if (
            not decision.allowed
            and self.approval_resolver is not None
            and effective_actor is not None
        ):
            decision = await self.approval_resolver.resolve(
                request,
                decision,
                actor=effective_actor,
                approval_id=approval_id,
            )
            self._validate_decision(request, decision)

        await self._record(request, decision, EgressAuditEventType.EVALUATED)
        await self._record(
            request,
            decision,
            EgressAuditEventType.ALLOWED if decision.allowed else EgressAuditEventType.DENIED,
        )
        return decision

    async def enforce(
        self,
        request: EgressRequest,
        *,
        actor: ActorIdentity | None = None,
        approval_id: str | None = None,
    ) -> EgressDecision:
        decision = await self.evaluate(request, actor=actor, approval_id=approval_id)
        if not decision.allowed:
            target_posture = _resolved_target_posture(request, decision)
            profile_ref = _resolved_profile_ref(request, decision)
            cost_class = _resolved_cost_class(request, decision)
            details: dict[str, JsonValue] = {
                "egress_request_id": request.request_id,
                "target_kind": request.target.kind.value,
                "target_id": request.target.target_id,
                "target_posture": target_posture.value,
                "classification": (
                    None if request.classification is None else request.classification.value
                ),
                "reason_code": decision.reason_code.value,
                "outcome": decision.outcome.value,
                "payload_digest": request.payload_digest,
                "policy_version": decision.policy_version,
            }
            if profile_ref is not None:
                details["profile_ref"] = profile_ref
            if cost_class is not None:
                details["cost_class"] = cost_class.value
            if decision.approval_ref is not None:
                details["approval_ref"] = decision.approval_ref
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "outbound data movement denied by egress policy",
                details=details,
            )
        return decision

    async def enforce_json(
        self,
        request: EgressRequest,
        payload: JsonValue,
        *,
        actor: ActorIdentity | None = None,
        approval_id: str | None = None,
    ) -> tuple[JsonValue, EgressDecision]:
        """Enforce policy and apply required field redactions before transport."""

        decision = await self.enforce(request, actor=actor, approval_id=approval_id)
        transformed = _apply_redactions(payload, decision.required_redactions)
        if request.classification is not None and decision.effective_classification is not None:
            downgraded = classification_strength(decision.effective_classification) < (
                classification_strength(request.classification)
            )
            require_monotonic_classification(
                request.classification,
                decision.effective_classification,
                redacted=downgraded and bool(decision.required_redactions),
                policy_decision_id=request.request_id if downgraded else None,
            )
            if downgraded and transformed == payload:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "egress policy downgraded classification without changing the payload",
                    details={"egress_request_id": request.request_id},
                )
        return transformed, decision

    @staticmethod
    def _validate_decision(request: EgressRequest, decision: EgressDecision) -> None:
        try:
            decision.validate_against(request)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "egress policy returned an invalid decision",
                details={
                    "egress_request_id": request.request_id,
                    "target_kind": request.target.kind.value,
                    "target_id": request.target.target_id,
                },
            ) from exc

    async def _record(
        self,
        request: EgressRequest,
        decision: EgressDecision,
        event_type: EgressAuditEventType,
    ) -> None:
        # Deliberately only project request identity, classification, digest and safe metadata.
        target_posture = _resolved_target_posture(request, decision)
        profile_ref = _resolved_profile_ref(request, decision)
        cost_class = _resolved_cost_class(request, decision)
        await self.audit_sink.record(
            EgressAuditEvent(
                event_type=event_type,
                request_id=request.request_id,
                target_kind=request.target.kind,
                target_id=request.target.target_id,
                target_posture=target_posture,
                classification=decision.effective_classification,
                outcome=decision.outcome,
                reason_code=decision.reason_code,
                payload_digest=request.payload_digest,
                policy_version=decision.policy_version,
                correlation_id=request.context.correlation_id,
                project_id=request.context.project_id,
                task_id=request.task_id,
                run_id=request.run_id,
                capability_id=request.capability_id,
                profile_ref=profile_ref,
                cost_class=cost_class,
                approval_ref=decision.approval_ref,
                audit_metadata=decision.audit_metadata,
            )
        )


def _decision_metadata_string(decision: EgressDecision, key: str) -> str | None:
    value = decision.audit_metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _resolved_target_posture(
    request: EgressRequest,
    decision: EgressDecision,
) -> EgressTargetPosture:
    value = _decision_metadata_string(decision, "target_posture")
    if value is not None:
        try:
            return EgressTargetPosture(value)
        except ValueError:
            pass
    return request.target.effective_posture


def _resolved_profile_ref(request: EgressRequest, decision: EgressDecision) -> str | None:
    return _decision_metadata_string(decision, "profile_ref") or request.target.profile_ref


def _resolved_cost_class(
    request: EgressRequest,
    decision: EgressDecision,
) -> EgressCostClass | None:
    value = _decision_metadata_string(decision, "cost_class")
    if value is not None:
        try:
            return EgressCostClass(value)
        except ValueError:
            pass
    profile = request.target.profile
    return None if profile is None else profile.cost_class


def _apply_redactions(payload: JsonValue, paths: tuple[str, ...]) -> JsonValue:
    if not paths:
        return payload
    if not isinstance(payload, dict):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "field-level egress redaction requires an object payload",
        )
    result: dict[str, JsonValue] = dict(payload)
    for path in paths:
        parts = path.split(".")
        _remove_path(result, parts)
    return result


def _remove_path(value: dict[str, JsonValue], parts: list[str]) -> None:
    if not parts:
        return
    head = parts[0]
    if len(parts) == 1:
        value.pop(head, None)
        return
    nested = value.get(head)
    if not isinstance(nested, dict):
        return
    detached = dict(nested)
    value[head] = detached
    _remove_path(detached, parts[1:])
