"""Application-owned fail-closed egress policy orchestration."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts.classification import (
    DataClassification,
    classification_strength,
    require_monotonic_classification,
)
from ai_multi_agent_platform.contracts.egress import (
    EgressAuditEvent,
    EgressAuditEventType,
    EgressAuditSink,
    EgressDecision,
    EgressOutcome,
    EgressPolicyPort,
    EgressReasonCode,
    EgressRequest,
    EgressTargetPosture,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue


class CanonicalEgressPolicy(EgressPolicyPort):
    """Conservative baseline policy for known application trust boundaries.

    Public/internal data may leave through a configured, known external target. Secret,
    secret-reference and regulated data are denied externally unless trusted target
    configuration explicitly opts into that exact classification and records
    ``allow_sensitive_external=true``. Unknown target posture and absent classification
    fail closed.
    """

    version = "egress-policy/v1"

    async def evaluate(self, request: EgressRequest) -> EgressDecision:
        classification = request.classification
        target = request.target
        if classification is None:
            return self._deny(request, EgressReasonCode.CLASSIFICATION_REQUIRED)
        if target.posture is EgressTargetPosture.UNKNOWN:
            return self._deny(request, EgressReasonCode.UNKNOWN_TARGET_POSTURE)
        if (
            target.allowed_classifications
            and classification not in target.allowed_classifications
        ):
            return self._deny(request, EgressReasonCode.TARGET_POLICY_DENIED)
        if target.posture is EgressTargetPosture.EXTERNAL and classification in {
            DataClassification.SECRET,
            DataClassification.SECRET_REFERENCE,
            DataClassification.REGULATED,
        }:
            explicitly_allowed = (
                classification in target.allowed_classifications
                and target.policy_metadata.get("allow_sensitive_external") is True
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
            audit_metadata={"target_posture": target.posture.value},
        )

    def _deny(self, request: EgressRequest, reason: EgressReasonCode) -> EgressDecision:
        return EgressDecision(
            request_id=request.request_id,
            outcome=EgressOutcome.DENY,
            target_kind=request.target.kind,
            target_id=request.target.target_id,
            effective_classification=request.classification,
            reason_code=reason,
            policy_version=self.version,
            audit_metadata={"target_posture": request.target.posture.value},
        )


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
    ) -> None:
        self.policy = policy or CanonicalEgressPolicy()
        self.audit_sink = audit_sink or NullEgressAuditSink()

    async def evaluate(self, request: EgressRequest) -> EgressDecision:
        decision = await self.policy.evaluate(request)
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
        await self._record(request, decision, EgressAuditEventType.EVALUATED)
        await self._record(
            request,
            decision,
            EgressAuditEventType.ALLOWED if decision.allowed else EgressAuditEventType.DENIED,
        )
        return decision

    async def enforce(self, request: EgressRequest) -> EgressDecision:
        decision = await self.evaluate(request)
        if not decision.allowed:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "outbound data movement denied by egress policy",
                details={
                    "egress_request_id": request.request_id,
                    "target_kind": request.target.kind.value,
                    "target_id": request.target.target_id,
                    "target_posture": request.target.posture.value,
                    "classification": (
                        None if request.classification is None else request.classification.value
                    ),
                    "reason_code": decision.reason_code.value,
                    "payload_digest": request.payload_digest,
                    "policy_version": decision.policy_version,
                },
            )
        return decision

    async def enforce_json(
        self,
        request: EgressRequest,
        payload: JsonValue,
    ) -> tuple[JsonValue, EgressDecision]:
        """Enforce policy and apply required field redactions before transport."""

        decision = await self.enforce(request)
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

    async def _record(
        self,
        request: EgressRequest,
        decision: EgressDecision,
        event_type: EgressAuditEventType,
    ) -> None:
        # Deliberately only project request identity, classification, digest and safe metadata.
        await self.audit_sink.record(
            EgressAuditEvent(
                event_type=event_type,
                request_id=request.request_id,
                target_kind=request.target.kind,
                target_id=request.target.target_id,
                target_posture=request.target.posture,
                classification=decision.effective_classification,
                outcome=decision.outcome,
                reason_code=decision.reason_code,
                payload_digest=request.payload_digest,
                policy_version=decision.policy_version,
                correlation_id=request.context.correlation_id,
                task_id=request.task_id,
                run_id=request.run_id,
                capability_id=request.capability_id,
                audit_metadata=decision.audit_metadata,
            )
        )


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
