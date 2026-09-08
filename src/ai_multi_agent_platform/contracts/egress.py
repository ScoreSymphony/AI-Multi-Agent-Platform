"""Provider-neutral contracts for policy-controlled outbound data movement."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .classification import DataClassification, classification_strength
from .types import JsonValue, OperationContext


class EgressTargetKind(StrEnum):
    MODEL_PROVIDER = "model_provider"
    CONNECTOR = "connector"
    CAPABILITY = "capability"
    CONTEXT_EXPORT = "context_export"
    FILE_EXPORT = "file_export"
    ARTIFACT_EXPORT = "artifact_export"
    API = "api"
    WORKER = "worker"


class EgressTargetPosture(StrEnum):
    LOCAL = "local"
    INTERNAL = "internal"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class EgressOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class EgressReasonCode(StrEnum):
    ALLOWED = "allowed"
    CLASSIFICATION_REQUIRED = "classification_required"
    UNKNOWN_TARGET_POSTURE = "unknown_target_posture"
    TARGET_POLICY_DENIED = "target_policy_denied"
    SENSITIVE_EXTERNAL_DENIED = "sensitive_external_denied"
    INVALID_POLICY_DECISION = "invalid_policy_decision"


class EgressAuditEventType(StrEnum):
    EVALUATED = "EgressEvaluated"
    DENIED = "EgressDenied"
    ALLOWED = "EgressAllowed"


@dataclass(frozen=True, slots=True)
class EgressTarget:
    """Application-owned description of the trust boundary receiving data."""

    kind: EgressTargetKind
    target_id: str
    posture: EgressTargetPosture = EgressTargetPosture.UNKNOWN
    allowed_classifications: tuple[DataClassification, ...] = ()
    policy_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.target_id.strip():
            raise ValueError("egress target_id must not be blank")
        if len(set(self.allowed_classifications)) != len(self.allowed_classifications):
            raise ValueError("egress target allowed classifications must be unique")
        object.__setattr__(self, "policy_metadata", dict(self.policy_metadata))


@dataclass(frozen=True, slots=True)
class EgressRequest:
    """Safe policy input containing classification and payload identity, never raw content."""

    request_id: str
    target: EgressTarget
    context: OperationContext
    classification: DataClassification | None
    resource_type: str
    payload_digest: str
    task_id: str | None = None
    run_id: str | None = None
    capability_id: str | None = None
    policy_descriptors: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("egress request_id must not be blank")
        if not self.resource_type.strip():
            raise ValueError("egress resource_type must not be blank")
        _validate_sha256(self.payload_digest, "egress payload_digest")
        for value, name in (
            (self.task_id, "egress task_id"),
            (self.run_id, "egress run_id"),
            (self.capability_id, "egress capability_id"),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank")
        object.__setattr__(self, "policy_descriptors", dict(self.policy_descriptors))


@dataclass(frozen=True, slots=True)
class EgressDecision:
    """Normalized policy result consumed uniformly by every outbound boundary."""

    request_id: str
    outcome: EgressOutcome
    target_kind: EgressTargetKind
    target_id: str
    effective_classification: DataClassification | None
    reason_code: EgressReasonCode
    policy_version: str
    required_redactions: tuple[str, ...] = ()
    minimized: bool = False
    audit_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("egress decision request_id must not be blank")
        if not self.target_id.strip():
            raise ValueError("egress decision target_id must not be blank")
        if not self.policy_version.strip():
            raise ValueError("egress decision policy_version must not be blank")
        if any(not path.strip() for path in self.required_redactions):
            raise ValueError("egress required redactions must not contain blanks")
        if len(set(self.required_redactions)) != len(self.required_redactions):
            raise ValueError("egress required redactions must be unique")
        object.__setattr__(self, "audit_metadata", dict(self.audit_metadata))

    @property
    def allowed(self) -> bool:
        return self.outcome is EgressOutcome.ALLOW

    def validate_against(self, request: EgressRequest) -> None:
        """Reject policy output that changes request/target identity or silently downgrades."""

        if self.request_id != request.request_id:
            raise ValueError("egress decision request_id does not match request")
        if (
            self.target_kind is not request.target.kind
            or self.target_id != request.target.target_id
        ):
            raise ValueError("egress decision target does not match request")
        if request.classification is None:
            if self.effective_classification is not None:
                raise ValueError("egress policy cannot invent classification for unclassified data")
            return
        if self.effective_classification is None:
            raise ValueError("classified egress request requires an effective classification")
        if classification_strength(self.effective_classification) < classification_strength(
            request.classification
        ) and not (self.required_redactions or self.minimized):
            raise ValueError(
                "egress classification downgrade requires explicit redaction/minimization"
            )


@dataclass(frozen=True, slots=True)
class EgressAuditEvent:
    """Value-free audit projection safe for Events/telemetry/persistence."""

    event_type: EgressAuditEventType
    request_id: str
    target_kind: EgressTargetKind
    target_id: str
    target_posture: EgressTargetPosture
    classification: DataClassification | None
    outcome: EgressOutcome
    reason_code: EgressReasonCode
    payload_digest: str
    policy_version: str
    correlation_id: str
    task_id: str | None = None
    run_id: str | None = None
    capability_id: str | None = None
    audit_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_sha256(self.payload_digest, "egress audit payload_digest")
        object.__setattr__(self, "audit_metadata", dict(self.audit_metadata))


@runtime_checkable
class EgressPolicyPort(Protocol):
    async def evaluate(self, request: EgressRequest) -> EgressDecision: ...


@runtime_checkable
class EgressAuditSink(Protocol):
    async def record(self, event: EgressAuditEvent) -> None: ...


def digest_egress_payload(payload: JsonValue | bytes | str) -> str:
    """Return a stable digest used for policy/audit binding without exposing payload values."""

    if isinstance(payload, bytes):
        data = payload
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _validate_sha256(value: str, field_name: str) -> None:
    if len(value) != 64:
        raise ValueError(f"{field_name} must contain 64 hexadecimal characters")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be hexadecimal") from exc
