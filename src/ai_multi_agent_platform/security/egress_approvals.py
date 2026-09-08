"""Exact #15 Approval binding for explicitly approvable egress exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressDecision,
    EgressOutcome,
    EgressReasonCode,
    EgressRequest,
    ErrorCode,
    JsonValue,
)

from .authorization import (
    ActorIdentity,
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    RiskClassification,
)
from .enforcement import AuthorizationGate


@dataclass(frozen=True, slots=True)
class EgressApprovalExceptionPolicy:
    """Deployment-owned declaration of which egress denials may become approval requests.

    The default policy approves nothing. Deployments must enumerate exception reason codes
    explicitly. Secret values/references remain non-overridable by default even when a reason is
    otherwise approvable. Approved records are reusable only for the exact action digest until the
    existing #15 Approval expires; changed content, target, profile revision, project or action
    creates a different digest and therefore invalidates the old approval.
    """

    approvable_reason_codes: frozenset[EgressReasonCode] = frozenset()
    approvable_classifications: frozenset[DataClassification] = field(
        default_factory=lambda: frozenset(
            {
                DataClassification.PUBLIC,
                DataClassification.INTERNAL,
                DataClassification.CONFIDENTIAL,
                DataClassification.PRIVATE,
                DataClassification.RESTRICTED,
                DataClassification.REGULATED,
            }
        )
    )
    non_overridable_classifications: frozenset[DataClassification] = field(
        default_factory=lambda: frozenset(
            {
                DataClassification.SECRET,
                DataClassification.SECRET_REFERENCE,
            }
        )
    )
    policy_id: str = "egress-approval-exception/v1"
    reuse_semantics: str = "exact_digest_until_expiry"

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("egress approval exception policy_id must not be blank")
        if not self.reuse_semantics.strip():
            raise ValueError("egress approval reuse_semantics must not be blank")

    def permits(self, request: EgressRequest, decision: EgressDecision) -> bool:
        classification = request.classification
        if classification is None:
            return False
        if classification in self.non_overridable_classifications:
            return False
        return (
            decision.reason_code in self.approvable_reason_codes
            and classification in self.approvable_classifications
        )


class EgressApprovalBridge:
    """Resolve optional egress exceptions through the existing Authorization/Approval lifecycle."""

    def __init__(
        self,
        authorization_gate: AuthorizationGate,
        policy: EgressApprovalExceptionPolicy,
    ) -> None:
        self.authorization_gate = authorization_gate
        self.policy = policy

    async def resolve(
        self,
        request: EgressRequest,
        decision: EgressDecision,
        *,
        actor: ActorIdentity,
        approval_id: str | None = None,
    ) -> EgressDecision:
        if decision.allowed or not self.policy.permits(request, decision):
            return decision

        action = egress_proposed_action(request, decision, actor=actor)
        approved_ref = self._valid_approval_ref(action, approval_id)
        if approved_ref is not None:
            return replace(
                decision,
                outcome=EgressOutcome.ALLOW,
                reason_code=EgressReasonCode.ALLOWED,
                approval_ref=approved_ref,
                audit_metadata={
                    **dict(decision.audit_metadata),
                    "approval_exception_for_reason": decision.reason_code.value,
                    "approval_reuse_semantics": self.policy.reuse_semantics,
                },
            )

        pending = await self.authorization_gate.ensure_pending_approval_with_event(
            action,
            reason=(
                "egress policy permits an explicit approval exception for "
                f"{decision.reason_code.value}"
            ),
            policy_id=self.policy.policy_id,
            risk=_approval_risk(request.classification),
        )
        return replace(
            decision,
            outcome=EgressOutcome.REQUIRE_APPROVAL,
            reason_code=EgressReasonCode.APPROVAL_REQUIRED,
            approval_ref=pending.approval_id,
            audit_metadata={
                **dict(decision.audit_metadata),
                "approval_exception_for_reason": decision.reason_code.value,
                "approval_reuse_semantics": self.policy.reuse_semantics,
                "requested_action_digest": pending.requested_action_digest,
            },
        )

    def _valid_approval_ref(
        self,
        action: ProposedAction,
        approval_id: str | None,
    ) -> str | None:
        approvals = self.authorization_gate.approvals
        if approval_id is not None:
            try:
                if approvals.valid_for(approval_id, action):
                    return approval_id
            except ContractError as exc:
                if exc.code is not ErrorCode.NOT_FOUND:
                    raise
            return None
        record = approvals.find_valid_for(action)
        return None if record is None else record.approval_id


def egress_proposed_action(
    request: EgressRequest,
    decision: EgressDecision,
    *,
    actor: ActorIdentity,
) -> ProposedAction:
    """Bind Approval to the exact value-free disclosure subject and policy revision.

    Durable repository-backed policies may resolve a profile after the transport adapter has
    constructed its request. In that case the canonical policy projects the exact resolved
    profile revision into ``decision.audit_metadata``. Approval binding therefore prefers the
    request-attached profile but safely falls back to those policy-produced metadata fields.
    """

    target = request.target
    profile = target.profile
    profile_ref = target.profile_ref or _metadata_string(decision, "profile_ref")
    profile_source_revision = (
        profile.source_revision
        if profile is not None
        else _metadata_string(decision, "source_revision")
    )
    profile_cost_class = (
        profile.cost_class.value
        if profile is not None
        else _metadata_string(decision, "cost_class")
    )
    profile_trust = (
        profile.trust.value
        if profile is not None
        else _metadata_string(decision, "profile_trust")
    )

    payload: dict[str, JsonValue] = {
        "egress_target_kind": target.kind.value,
        "egress_target_id": target.target_id,
        "egress_target_posture": target.effective_posture.value,
        "data_classification": (
            None if request.classification is None else request.classification.value
        ),
        "payload_digest": request.payload_digest,
        "egress_resource_type": request.resource_type,
        "capability_id": request.capability_id,
        "egress_policy_version": decision.policy_version,
        "egress_profile_ref": profile_ref,
        "egress_profile_source_revision": profile_source_revision,
        "egress_cost_class": profile_cost_class,
        "egress_profile_trust": profile_trust,
    }
    return ProposedAction(
        AuthorizationContext(
            actor=actor,
            action=AuthorizationAction.EXECUTE,
            resource_type=_resource_type(target.kind.value),
            resource_id=target.target_id,
            operation=request.context,
            task_id=request.task_id,
            run_id=request.run_id,
            capability_ref=request.capability_id,
            side_effect="data_egress",
            security_labels=(
                "data_egress",
                (
                    "classification:unknown"
                    if request.classification is None
                    else f"classification:{request.classification.value}"
                ),
            ),
            trust_context={
                "egress_target_posture": target.effective_posture.value,
                "egress_profile_ref": profile_ref,
                "egress_profile_source_revision": profile_source_revision,
                "egress_policy_version": decision.policy_version,
            },
        ),
        payload=payload,
        payload_ref=f"egress-subject:sha256:{request.payload_digest}",
    )


def _metadata_string(decision: EgressDecision, key: str) -> str | None:
    value = decision.audit_metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _resource_type(target_kind: str) -> ResourceType:
    mapping = {
        "model_provider": ResourceType.MODEL_CONFIGURATION,
        "connector": ResourceType.CONNECTOR,
        "capability": ResourceType.CAPABILITY,
        "file_export": ResourceType.FILE,
        "artifact_export": ResourceType.ARTIFACT,
        "worker": ResourceType.WORKER,
    }
    return mapping.get(target_kind, ResourceType.GENERIC)


def _approval_risk(classification: DataClassification | None) -> RiskClassification:
    if classification in {DataClassification.RESTRICTED, DataClassification.REGULATED}:
        return RiskClassification.HIGH
    if classification in {
        DataClassification.CONFIDENTIAL,
        DataClassification.PRIVATE,
    }:
        return RiskClassification.ELEVATED
    return RiskClassification.STANDARD
