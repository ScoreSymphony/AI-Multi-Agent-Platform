"""Optional Pipelock Core projection/evidence adapter for canonical egress decisions.

The platform remains the policy authority. This module never evaluates authorization or data-egress
policy itself; it only projects an already-canonical ``EgressDecision`` into an adapter directive
and normalizes Pipelock receipt metadata as non-canonical evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ai_multi_agent_platform.contracts import EgressDecision, EgressOutcome, EgressRequest

PIPELOCK_ADAPTER_ID = "pipelock-core"
PIPELOCK_UPSTREAM_REPOSITORY = "https://github.com/luckyPipewrench/pipelock"
PIPELOCK_PINNED_REVISION = "f7d1816f1a5ad63d501b0c48f36066f836f59022"


class PipelockAdapterMode(StrEnum):
    """How an explicitly configured execution profile uses Pipelock."""

    DISABLED = "disabled"
    AUDIT_ONLY = "audit_only"
    ENFORCE = "enforce"


class PipelockDirective(StrEnum):
    """Adapter-local action after canonical policy has already decided the request."""

    SKIP = "skip"
    AUDIT = "audit"
    MEDIATED_ALLOW = "mediated_allow"
    BLOCK = "block"


class PipelockUnavailableAction(StrEnum):
    """Platform behavior when a projection cannot reach the optional Pipelock runtime."""

    NOT_REQUIRED = "not_required"
    CONTINUE_WITH_DEGRADED_EVIDENCE = "continue_with_degraded_evidence"
    FAIL_CLOSED = "fail_closed"


class ReceiptVerification(StrEnum):
    """External cryptographic verification state for Pipelock evidence."""

    NOT_CHECKED = "not_checked"
    VERIFIED = "verified"
    FAILED = "failed"


class PipelockMappingError(ValueError):
    """Raised when an upstream/canonical value cannot be mapped safely."""


@dataclass(frozen=True, slots=True)
class PipelockProjection:
    """Value-free projection of one canonical egress decision into Pipelock handling."""

    request_id: str
    correlation_id: str
    project_id: str | None
    task_id: str | None
    run_id: str | None
    agent_id: str | None
    capability_id: str | None
    owner_type: str | None
    owner_id: str | None
    target_kind: str
    target_id: str
    canonical_outcome: EgressOutcome
    directive: PipelockDirective
    platform_policy_version: str
    decision_digest: str
    approval_ref: str | None
    mode: PipelockAdapterMode
    adapter_id: str = PIPELOCK_ADAPTER_ID
    upstream_revision: str = PIPELOCK_PINNED_REVISION

    @property
    def requires_pipelock(self) -> bool:
        return self.directive in {PipelockDirective.AUDIT, PipelockDirective.MEDIATED_ALLOW}


@dataclass(frozen=True, slots=True)
class PipelockReceiptEvidence:
    """Redacted adapter evidence; never canonical policy or lifecycle truth."""

    request_id: str
    correlation_id: str
    project_id: str | None
    task_id: str | None
    run_id: str | None
    agent_id: str | None
    capability_id: str | None
    canonical_outcome: EgressOutcome
    platform_policy_version: str
    decision_digest: str
    pipelock_action_id: str
    pipelock_verdict: str
    transport: str
    method: str | None
    layer: str | None
    pipelock_policy_hash: str | None
    destination_digest: str | None
    verification: ReceiptVerification
    adapter_id: str = PIPELOCK_ADAPTER_ID
    upstream_revision: str = PIPELOCK_PINNED_REVISION


def project_egress_decision(
    request: EgressRequest,
    decision: EgressDecision,
    *,
    mode: PipelockAdapterMode,
) -> PipelockProjection:
    """Project canonical #591 output without granting or redefining permissions.

    Canonical non-allow outcomes are blocked before an upstream enforcement adapter is consulted.
    Only an explicit canonical ``ALLOW`` may become audit traffic or a mediated allow. Unknown
    future outcomes fail closed through ``PipelockMappingError`` rather than being optimistically
    forwarded.
    """

    decision.validate_against(request)

    if decision.outcome is EgressOutcome.ALLOW:
        directive = {
            PipelockAdapterMode.DISABLED: PipelockDirective.SKIP,
            PipelockAdapterMode.AUDIT_ONLY: PipelockDirective.AUDIT,
            PipelockAdapterMode.ENFORCE: PipelockDirective.MEDIATED_ALLOW,
        }[mode]
    elif decision.outcome in {
        EgressOutcome.DENY,
        EgressOutcome.REQUIRE_APPROVAL,
        EgressOutcome.LOCAL_ONLY,
        EgressOutcome.UNKNOWN_BLOCKED,
    }:
        directive = PipelockDirective.BLOCK
    else:  # pragma: no cover - defensive against a future enum member
        raise PipelockMappingError(f"unmapped canonical egress outcome: {decision.outcome!r}")

    return PipelockProjection(
        request_id=request.request_id,
        correlation_id=request.context.correlation_id,
        project_id=request.context.project_id,
        task_id=request.task_id,
        run_id=request.run_id,
        agent_id=_optional_text(request.policy_descriptors.get("agent_id")),
        capability_id=request.capability_id,
        owner_type=request.context.owner_type,
        owner_id=request.context.owner_id,
        target_kind=decision.target_kind.value,
        target_id=decision.target_id,
        canonical_outcome=decision.outcome,
        directive=directive,
        platform_policy_version=decision.policy_version,
        decision_digest=_decision_digest(request, decision),
        approval_ref=decision.approval_ref,
        mode=mode,
    )


def resolve_pipelock_unavailable(projection: PipelockProjection) -> PipelockUnavailableAction:
    """Resolve outage behavior without changing the canonical egress decision.

    Audit-only operation may continue with explicitly degraded evidence because canonical policy has
    already allowed the request. Enforced mediation fails closed. Projections that never needed the
    optional runtime remain unaffected by its availability.
    """

    if not projection.requires_pipelock:
        return PipelockUnavailableAction.NOT_REQUIRED
    if projection.mode is PipelockAdapterMode.AUDIT_ONLY:
        return PipelockUnavailableAction.CONTINUE_WITH_DEGRADED_EVIDENCE
    if projection.mode is PipelockAdapterMode.ENFORCE:
        return PipelockUnavailableAction.FAIL_CLOSED
    raise PipelockMappingError(
        f"projection requires Pipelock in unsupported adapter mode: {projection.mode!r}"
    )


def normalize_pipelock_receipt(
    projection: PipelockProjection,
    receipt: Mapping[str, object],
    *,
    verification: ReceiptVerification = ReceiptVerification.NOT_CHECKED,
) -> PipelockReceiptEvidence:
    """Normalize one receipt without persisting raw destination/pattern payload data.

    Cryptographic verification is supplied by the verifier call site. A self-asserted field inside a
    receipt can therefore never promote itself to ``VERIFIED``.
    """

    action_id = _required_text(receipt.get("action_id"), "action_id")
    verdict = _required_text(receipt.get("verdict"), "verdict")
    transport = _required_text(receipt.get("transport"), "transport")
    destination = _optional_text(receipt.get("target"))

    return PipelockReceiptEvidence(
        request_id=projection.request_id,
        correlation_id=projection.correlation_id,
        project_id=projection.project_id,
        task_id=projection.task_id,
        run_id=projection.run_id,
        agent_id=projection.agent_id,
        capability_id=projection.capability_id,
        canonical_outcome=projection.canonical_outcome,
        platform_policy_version=projection.platform_policy_version,
        decision_digest=projection.decision_digest,
        pipelock_action_id=action_id,
        pipelock_verdict=verdict,
        transport=transport,
        method=_optional_text(receipt.get("method")),
        layer=_optional_text(receipt.get("layer")),
        pipelock_policy_hash=_optional_text(receipt.get("policy_hash")),
        destination_digest=None if destination is None else _text_digest(destination),
        verification=verification,
    )


def _decision_digest(request: EgressRequest, decision: EgressDecision) -> str:
    payload = {
        "request_id": request.request_id,
        "payload_digest": request.payload_digest,
        "profile_ref": request.target.profile_ref,
        "target_kind": decision.target_kind.value,
        "target_id": decision.target_id,
        "outcome": decision.outcome.value,
        "effective_classification": (
            None
            if decision.effective_classification is None
            else decision.effective_classification.value
        ),
        "reason_code": decision.reason_code.value,
        "policy_version": decision.policy_version,
        "required_redactions": list(decision.required_redactions),
        "minimized": decision.minimized,
        "approval_ref": decision.approval_ref,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_text(value: object, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise PipelockMappingError(f"Pipelock receipt field {field_name!r} must be non-empty text")
    return text


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
