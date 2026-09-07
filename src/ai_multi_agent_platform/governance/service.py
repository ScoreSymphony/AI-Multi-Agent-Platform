"""Application service for optional Proposal/Specification governance (#501)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import PlatformKernel, TaskState
from ai_multi_agent_platform.security import (
    ApprovalRecord,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)

from .models import (
    ConversionStatus,
    GovernanceAuditEvent,
    Proposal,
    ProposalStatus,
    SpecificationRevision,
    TaskConversion,
)
from .repository import GovernanceRepository


@dataclass(frozen=True, slots=True)
class GovernanceCallContext:
    actor_ref: str
    correlation_id: str
    idempotency_key: str | None = None
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if not self.actor_ref.strip():
            raise ValueError("governance actor_ref must not be blank")
        if not self.correlation_id.strip():
            raise ValueError("governance correlation_id must not be blank")


@dataclass(frozen=True, slots=True)
class ApprovedSpecificationPlanningInput:
    """Immutable #439-facing view; planning consumes but cannot rewrite governance state."""

    specification_id: str
    revision: int
    content_digest: str
    goal: str
    acceptance_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    decomposition_hints: tuple[str, ...]
    required_tests: tuple[str, ...]
    verification_requirements: tuple[str, ...]


class GovernanceService:
    """Own optional governance state while canonical Task remains the execution unit."""

    def __init__(
        self,
        repository: GovernanceRepository,
        kernel: PlatformKernel,
        approval_gate: AuthorizationGate,
    ) -> None:
        self.repository = repository
        self.kernel = kernel
        self.approval_gate = approval_gate

    def create_proposal(self, proposal: Proposal, *, actor_ref: str) -> Proposal:
        created = self.repository.create_proposal(proposal)
        self._audit(
            "proposal.created",
            "proposal",
            created.id,
            actor_ref,
            created.project_id,
            revision=created.revision,
            metadata={"status": created.status.value, "source": created.source},
        )
        return created

    def create_proposal_from_signal(
        self,
        *,
        title: str,
        summary: str,
        reason: str,
        owner_ref: OwnerRef,
        requester_ref: str,
        source: str,
        project_id: str | None = None,
        workspace_id: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        confidence: float | None = None,
        expected_value: float | None = None,
        risk: RiskClassification = RiskClassification.STANDARD,
        fingerprint: str | None = None,
    ) -> Proposal:
        """Optional Automation/monitoring intake without bypassing governance ownership."""

        proposal = Proposal(
            title=title,
            summary=summary,
            reason=reason,
            owner_ref=owner_ref,
            requester_ref=requester_ref,
            source=source,
            project_id=project_id,
            workspace_id=workspace_id,
            evidence_refs=evidence_refs,
            confidence=confidence,
            expected_value=expected_value,
            risk=risk,
            fingerprint=fingerprint,
            status=ProposalStatus.PROPOSED,
            provenance={"intake": "signal"},
        )
        return self.create_proposal(proposal, actor_ref=requester_ref)

    def revise_proposal(
        self,
        proposal: Proposal,
        *,
        expected_revision: int,
        actor_ref: str,
    ) -> Proposal:
        current = self.repository.get_proposal(proposal.id)
        if current.status in {
            ProposalStatus.DISMISSED,
            ProposalStatus.SUPERSEDED,
            ProposalStatus.CONVERTED_TO_TASK,
        }:
            raise ContractError(ErrorCode.CONFLICT, "terminal proposal cannot be revised")
        revised = self.repository.revise_proposal(proposal, expected_revision=expected_revision)
        self._audit(
            "proposal.revised",
            "proposal",
            revised.id,
            actor_ref,
            revised.project_id,
            revision=revised.revision,
        )
        return revised

    def dismiss_proposal(
        self, proposal_id: str, *, expected_revision: int, actor_ref: str
    ) -> Proposal:
        current = self.repository.get_proposal(proposal_id)
        if current.status is ProposalStatus.CONVERTED_TO_TASK:
            raise ContractError(ErrorCode.CONFLICT, "converted proposal cannot be dismissed")
        if current.status is ProposalStatus.DISMISSED:
            return current
        updated = replace(
            current,
            status=ProposalStatus.DISMISSED,
            revision=current.revision + 1,
            updated_at=datetime.now(UTC),
        )
        persisted = self.repository.revise_proposal(updated, expected_revision=expected_revision)
        self._audit(
            "proposal.dismissed",
            "proposal",
            proposal_id,
            actor_ref,
            current.project_id,
            revision=persisted.revision,
        )
        return persisted

    def supersede_proposal(
        self,
        proposal_id: str,
        replacement: Proposal,
        *,
        expected_revision: int,
        actor_ref: str,
    ) -> tuple[Proposal, Proposal]:
        current = self.repository.get_proposal(proposal_id)
        if current.status in {ProposalStatus.SUPERSEDED, ProposalStatus.CONVERTED_TO_TASK}:
            raise ContractError(
                ErrorCode.CONFLICT, "proposal cannot be superseded from current state"
            )
        if replacement.supersedes_id != proposal_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replacement proposal must reference supersedes_id",
            )
        created = self.repository.create_proposal(replacement)
        superseded = replace(
            current,
            status=ProposalStatus.SUPERSEDED,
            superseded_by_id=created.id,
            revision=current.revision + 1,
            updated_at=datetime.now(UTC),
        )
        try:
            persisted = self.repository.revise_proposal(
                superseded, expected_revision=expected_revision
            )
        except Exception:
            # The replacement remains an auditable intake artifact rather than being
            # silently deleted. Its supersedes link makes the interrupted operation clear.
            self._audit(
                "proposal.supersession-incomplete",
                "proposal",
                created.id,
                actor_ref,
                created.project_id,
                revision=created.revision,
                metadata={"supersedes_id": proposal_id},
            )
            raise
        self._audit(
            "proposal.superseded",
            "proposal",
            proposal_id,
            actor_ref,
            current.project_id,
            revision=persisted.revision,
            metadata={"superseded_by_id": created.id},
        )
        return persisted, created

    def create_specification(
        self, specification: SpecificationRevision, *, actor_ref: str
    ) -> SpecificationRevision:
        if specification.revision != 1:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "new specification must start at revision 1"
            )
        if specification.proposal_id is not None:
            proposal = self.repository.get_proposal(specification.proposal_id)
            self._require_same_scope(proposal, specification)
            if proposal.status in {
                ProposalStatus.DISMISSED,
                ProposalStatus.SUPERSEDED,
                ProposalStatus.CONVERTED_TO_TASK,
            }:
                raise ContractError(ErrorCode.CONFLICT, "proposal cannot receive a specification")
        created = self.repository.create_specification(specification)
        if created.proposal_id is not None:
            self._mark_proposal_ready(created.proposal_id)
        self._audit_spec("specification.created", created, actor_ref)
        return created

    def revise_specification(
        self,
        specification: SpecificationRevision,
        *,
        expected_revision: int,
        actor_ref: str,
    ) -> SpecificationRevision:
        current = self.repository.get_specification(specification.id)
        if specification.proposal_id != current.proposal_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "specification intake reference is immutable"
            )
        if (
            specification.goal_id != current.goal_id
            or specification.task_intake_id != current.task_intake_id
        ):
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "specification intake reference is immutable"
            )
        if (
            specification.project_id != current.project_id
            or specification.workspace_id != current.workspace_id
        ):
            raise ContractError(ErrorCode.INVALID_REQUEST, "specification scope is immutable")
        revised = self.repository.revise_specification(
            specification, expected_revision=expected_revision
        )
        self._audit_spec("specification.revised", revised, actor_ref)
        return revised

    async def request_approval(
        self,
        specification_id: str,
        *,
        context: GovernanceCallContext,
    ) -> ApprovalRecord:
        specification = self.repository.get_specification(specification_id)
        action = self._conversion_action(specification, context.actor_ref, context.correlation_id)
        approval = await self.approval_gate.ensure_pending_approval_with_event(
            action,
            reason="Specification requires review before Task conversion",
            policy_id="governance:specification-conversion",
            risk=specification.risk,
        )
        self._audit_spec(
            "specification.approval-requested",
            specification,
            context.actor_ref,
            metadata={"approval_id": approval.approval_id},
        )
        return approval

    async def convert_to_task(
        self,
        specification_id: str,
        *,
        context: GovernanceCallContext,
    ) -> TaskState:
        specification = self.repository.get_specification(specification_id)
        action = self._conversion_action(specification, context.actor_ref, context.correlation_id)
        approval_id = context.approval_id

        if specification.approval_required:
            if approval_id is None:
                valid = self.approval_gate.approvals.find_valid_for(action)
                approval_id = valid.approval_id if valid is not None else None
            if approval_id is None or not self.approval_gate.approvals.valid_for(
                approval_id, action
            ):
                if approval_id is not None:
                    self._audit_spec(
                        "specification.stale-approval-rejected",
                        specification,
                        context.actor_ref,
                        metadata={"approval_id": approval_id},
                    )
                pending = await self.approval_gate.ensure_pending_approval_with_event(
                    action,
                    reason="Specification requires review before Task conversion",
                    policy_id="governance:specification-conversion",
                    risk=specification.risk,
                )
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "exact Specification revision requires approval before Task conversion",
                    details={
                        "approval_id": pending.approval_id,
                        "specification_id": specification.id,
                        "revision": specification.revision,
                        "content_digest": specification.content_digest,
                    },
                )

        existing = self.repository.get_conversion(specification.id)
        if existing is not None and (
            existing.specification_revision != specification.revision
            or existing.specification_digest != specification.content_digest
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Specification was revised after its Task conversion was reserved",
            )

        reserved = self.repository.reserve_conversion(
            existing
            or TaskConversion(
                specification_id=specification.id,
                specification_revision=specification.revision,
                specification_digest=specification.content_digest,
                proposal_id=specification.proposal_id,
                task_id=new_id("task"),
                approval_id=approval_id,
            )
        )
        task_title, task_objective = self._task_text(specification)
        base_key = (
            f"governance.convert:{specification.id}:{specification.revision}:"
            f"{specification.content_digest}"
        )
        task = await self.kernel.create_task(
            idempotency_key=base_key,
            title=task_title,
            objective=task_objective,
            owner_type=specification.owner_ref.type,
            owner_id=specification.owner_ref.id,
            project_id=specification.project_id,
            task_id=reserved.task_id,
            actor_ref=context.actor_ref,
            source="proposal-specification-governance",
        )
        task = await self.kernel.update_task(
            idempotency_key=f"{base_key}:provenance",
            task_id=reserved.task_id,
            metadata={"governance": self._task_governance_metadata(specification, approval_id)},
            actor_ref=context.actor_ref,
            source="proposal-specification-governance",
        )
        completed = self.repository.complete_conversion(specification.id, approval_id=approval_id)
        if specification.proposal_id is not None:
            self._mark_proposal_converted(specification.proposal_id, completed.task_id)
        self._audit(
            "specification.converted-to-task",
            "conversion",
            specification.id,
            context.actor_ref,
            specification.project_id,
            revision=specification.revision,
            digest=specification.content_digest,
            metadata={"task_id": completed.task_id, "approval_id": approval_id},
        )
        return task

    def planning_input(
        self,
        specification_id: str,
        revision: int,
        *,
        actor_ref: str,
        correlation_id: str,
    ) -> ApprovedSpecificationPlanningInput:
        current = self.repository.get_specification(specification_id)
        if revision != current.revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "planning may consume only the current exact Specification revision",
            )
        if current.approval_required:
            action = self._conversion_action(current, actor_ref, correlation_id)
            if self.approval_gate.approvals.find_valid_for(action) is None:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "planning input requires approval of the exact Specification revision",
                )
        return ApprovedSpecificationPlanningInput(
            specification_id=current.id,
            revision=current.revision,
            content_digest=current.content_digest,
            goal=current.goal,
            acceptance_criteria=current.acceptance_criteria,
            constraints=current.constraints,
            decomposition_hints=current.decomposition_hints,
            required_tests=current.required_tests,
            verification_requirements=current.verification_requirements,
        )

    def _conversion_action(
        self, specification: SpecificationRevision, actor_ref: str, correlation_id: str
    ) -> ProposedAction:
        actor = infer_actor_identity(actor_ref)
        operation = OperationContext(
            correlation_id=correlation_id,
            owner_type=specification.owner_ref.type,
            owner_id=specification.owner_ref.id,
            project_id=specification.project_id,
        )
        return ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=AuthorizationAction.EXECUTE,
                resource_type=ResourceType.GENERIC,
                resource_id=specification.id,
                operation=operation,
                workspace_id=specification.workspace_id,
                side_effect="convert_specification_to_task",
                security_labels=("governance", "specification", "task-conversion"),
                trust_context={"canonical_domain": "proposal-specification-governance"},
            ),
            payload={
                "specification_id": specification.id,
                "revision": specification.revision,
                "content_digest": specification.content_digest,
            },
            payload_ref=(
                f"specification:{specification.id}:revision:{specification.revision}:"
                f"sha256:{specification.content_digest}"
            ),
        )

    def _task_text(self, specification: SpecificationRevision) -> tuple[str, str]:
        if specification.proposal_id is not None:
            proposal = self.repository.get_proposal(specification.proposal_id)
            return proposal.title, specification.goal
        return specification.goal[:120], specification.goal

    @staticmethod
    def _task_governance_metadata(
        specification: SpecificationRevision, approval_id: str | None
    ) -> dict[str, JsonValue]:
        return {
            "proposal_id": specification.proposal_id,
            "specification_id": specification.id,
            "specification_revision": specification.revision,
            "specification_digest": specification.content_digest,
            "approval_id": approval_id,
            "acceptance_criteria": list(specification.acceptance_criteria),
            "constraints": list(specification.constraints),
            "risk": specification.risk.value,
            "required_capabilities": list(specification.required_capabilities),
            "required_tests": list(specification.required_tests),
            "verification_requirements": list(specification.verification_requirements),
            "required_human_gates": list(specification.required_human_gates),
        }

    @staticmethod
    def _require_same_scope(proposal: Proposal, specification: SpecificationRevision) -> None:
        if proposal.project_id != specification.project_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "Proposal/Specification project mismatch"
            )
        if proposal.workspace_id != specification.workspace_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "Proposal/Specification workspace mismatch"
            )
        if proposal.owner_ref != specification.owner_ref:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Proposal/Specification owner mismatch")

    def _mark_proposal_ready(self, proposal_id: str) -> None:
        current = self.repository.get_proposal(proposal_id)
        if current.status is ProposalStatus.READY:
            return
        updated = replace(
            current,
            status=ProposalStatus.READY,
            revision=current.revision + 1,
            updated_at=datetime.now(UTC),
        )
        self.repository.revise_proposal(updated, expected_revision=current.revision)

    def _mark_proposal_converted(self, proposal_id: str, task_id: str) -> None:
        current = self.repository.get_proposal(proposal_id)
        if current.status is ProposalStatus.CONVERTED_TO_TASK:
            if current.converted_task_id != task_id:
                raise ContractError(ErrorCode.CONTRACT_VIOLATION, "proposal maps to multiple Tasks")
            return
        updated = replace(
            current,
            status=ProposalStatus.CONVERTED_TO_TASK,
            converted_task_id=task_id,
            revision=current.revision + 1,
            updated_at=datetime.now(UTC),
        )
        self.repository.revise_proposal(updated, expected_revision=current.revision)

    def _audit_spec(
        self,
        event_type: str,
        specification: SpecificationRevision,
        actor_ref: str,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        self._audit(
            event_type,
            "specification",
            specification.id,
            actor_ref,
            specification.project_id,
            revision=specification.revision,
            digest=specification.content_digest,
            metadata=metadata,
        )

    def _audit(
        self,
        event_type: str,
        resource_type: str,
        resource_id: str,
        actor_ref: str,
        project_id: str | None,
        *,
        revision: int | None = None,
        digest: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        if resource_type not in {"proposal", "specification", "conversion"}:
            raise ValueError("invalid governance audit resource type")
        self.repository.append_audit(
            GovernanceAuditEvent(
                event_type=event_type,
                resource_type=resource_type,  # type: ignore[arg-type]
                resource_id=resource_id,
                actor_ref=actor_ref,
                project_id=project_id,
                revision=revision,
                digest=digest,
                metadata=metadata or {},
            )
        )
