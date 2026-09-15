"""Awaitable Governance runtime facade over the synchronous compatibility service."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import TaskState
from ai_multi_agent_platform.security import ApprovalRecord

from .async_persistence import GovernancePersistenceOffload, governance_persistence_offload
from .models import (
    ConversionStatus,
    GovernanceAuditEvent,
    Proposal,
    ProposalStatus,
    SpecificationRevision,
    TaskConversion,
)
from .service import GovernanceCallContext, GovernanceService

_T = TypeVar("_T")


class AsyncGovernanceRuntime:
    """Canonical asyncio-safe Governance boundary for Control Plane callers."""

    def __init__(
        self,
        service: GovernanceService,
        *,
        persistence_offload: GovernancePersistenceOffload | None = None,
    ) -> None:
        self.service = service
        self.repository = service.repository
        self._offload = governance_persistence_offload(
            self.repository,
            owner=self,
            requested=persistence_offload,
        )

    async def _run(self, operation: Callable[[], _T], *, message: str) -> _T:
        return await self._offload.run(operation, message=message)

    async def create_proposal(self, proposal: Proposal, *, actor_ref: str) -> Proposal:
        return await self._run(
            lambda: self.service.create_proposal(proposal, actor_ref=actor_ref),
            message="failed to create governance proposal",
        )

    async def revise_proposal(
        self,
        proposal: Proposal,
        *,
        expected_revision: int,
        actor_ref: str,
    ) -> Proposal:
        return await self._run(
            lambda: self.service.revise_proposal(
                proposal,
                expected_revision=expected_revision,
                actor_ref=actor_ref,
            ),
            message="failed to revise governance proposal",
        )

    async def request_clarification(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
        actor_ref: str,
    ) -> Proposal:
        return await self._run(
            lambda: self.service.request_clarification(
                proposal_id,
                expected_revision=expected_revision,
                actor_ref=actor_ref,
            ),
            message="failed to update governance proposal clarification state",
        )

    async def dismiss_proposal(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
        actor_ref: str,
    ) -> Proposal:
        return await self._run(
            lambda: self.service.dismiss_proposal(
                proposal_id,
                expected_revision=expected_revision,
                actor_ref=actor_ref,
            ),
            message="failed to dismiss governance proposal",
        )

    async def supersede_proposal(
        self,
        proposal_id: str,
        replacement: Proposal,
        *,
        expected_revision: int,
        actor_ref: str,
    ) -> tuple[Proposal, Proposal]:
        return await self._run(
            lambda: self.service.supersede_proposal(
                proposal_id,
                replacement,
                expected_revision=expected_revision,
                actor_ref=actor_ref,
            ),
            message="failed to supersede governance proposal",
        )

    async def create_specification(
        self,
        specification: SpecificationRevision,
        *,
        actor_ref: str,
    ) -> SpecificationRevision:
        return await self._run(
            lambda: self.service.create_specification(specification, actor_ref=actor_ref),
            message="failed to create governance specification",
        )

    async def revise_specification(
        self,
        specification: SpecificationRevision,
        *,
        expected_revision: int,
        actor_ref: str,
    ) -> SpecificationRevision:
        return await self._run(
            lambda: self.service.revise_specification(
                specification,
                expected_revision=expected_revision,
                actor_ref=actor_ref,
            ),
            message="failed to revise governance specification",
        )

    async def get_proposal(self, proposal_id: str, revision: int | None = None) -> Proposal:
        return await self._run(
            lambda: self.repository.get_proposal(proposal_id, revision),
            message="failed to read governance proposal",
        )

    async def list_proposals(self) -> tuple[Proposal, ...]:
        return await self._run(
            self.repository.list_proposals,
            message="failed to list governance proposals",
        )

    async def proposal_history(self, proposal_id: str) -> tuple[Proposal, ...]:
        return await self._run(
            lambda: self.repository.proposal_history(proposal_id),
            message="failed to read governance proposal history",
        )

    async def get_specification(
        self,
        specification_id: str,
        revision: int | None = None,
    ) -> SpecificationRevision:
        return await self._run(
            lambda: self.repository.get_specification(specification_id, revision),
            message="failed to read governance specification",
        )

    async def list_specifications(self) -> tuple[SpecificationRevision, ...]:
        return await self._run(
            self.repository.list_specifications,
            message="failed to list governance specifications",
        )

    async def specification_history(
        self,
        specification_id: str,
    ) -> tuple[SpecificationRevision, ...]:
        return await self._run(
            lambda: self.repository.specification_history(specification_id),
            message="failed to read governance specification history",
        )

    async def list_audit(self) -> tuple[GovernanceAuditEvent, ...]:
        return await self._run(
            self.repository.list_audit,
            message="failed to list governance audit events",
        )

    async def request_approval(
        self,
        specification_id: str,
        *,
        context: GovernanceCallContext,
    ) -> ApprovalRecord:
        specification = await self.get_specification(specification_id)
        action = self.service._conversion_action(
            specification,
            context.actor_ref,
            context.correlation_id,
        )
        approval = await self.service.approval_gate.ensure_pending_approval_with_event(
            action,
            reason="Specification requires review before Task conversion",
            policy_id="governance:specification-conversion",
            risk=specification.risk,
        )
        await self._run(
            lambda: self.service._audit_spec(
                "specification.approval-requested",
                specification,
                context.actor_ref,
                metadata={"approval_id": approval.approval_id},
            ),
            message="failed to persist governance approval audit",
        )
        return approval

    async def convert_to_task(
        self,
        specification_id: str,
        *,
        context: GovernanceCallContext,
    ) -> TaskState:
        specification, existing = await self._run(
            lambda: (
                self.repository.get_specification(specification_id),
                self.repository.get_conversion(specification_id),
            ),
            message="failed to load governance conversion state",
        )
        if existing is not None and (
            existing.specification_revision != specification.revision
            or existing.specification_digest != specification.content_digest
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Specification was revised after its Task conversion was reserved",
            )

        proposal: Proposal | None = None
        if existing is not None and existing.status is ConversionStatus.COMPLETED:
            task = await self.service.kernel.get_task(existing.task_id)
            replay_proposal_id = specification.proposal_id
            if replay_proposal_id is not None:
                replay_proposal = await self.get_proposal(replay_proposal_id)
                if replay_proposal.status in {
                    ProposalStatus.DISMISSED,
                    ProposalStatus.SUPERSEDED,
                }:
                    await self._run(
                        lambda: self.service._audit(
                            "proposal.completed-conversion-state-drift",
                            "proposal",
                            replay_proposal.id,
                            context.actor_ref,
                            replay_proposal.project_id,
                            revision=replay_proposal.revision,
                            metadata={
                                "status": replay_proposal.status.value,
                                "task_id": existing.task_id,
                                "specification_id": specification.id,
                            },
                        ),
                        message="failed to persist governance conversion drift audit",
                    )
                else:
                    await self._run(
                        lambda: self.service._mark_proposal_converted(
                            replay_proposal.id,
                            existing.task_id,
                        ),
                        message="failed to persist converted governance proposal",
                    )
            await self._run(
                lambda: self.service._audit(
                    "specification.conversion-replayed",
                    "conversion",
                    specification.id,
                    context.actor_ref,
                    specification.project_id,
                    revision=specification.revision,
                    digest=specification.content_digest,
                    metadata={
                        "task_id": existing.task_id,
                        "approval_id": existing.approval_id,
                    },
                ),
                message="failed to persist governance conversion replay audit",
            )
            return task

        proposal_id = specification.proposal_id
        if proposal_id is not None:
            proposal = await self.get_proposal(proposal_id)
            self.service._require_non_terminal_proposal(proposal, "be converted to a Task")

        action = self.service._conversion_action(
            specification,
            context.actor_ref,
            context.correlation_id,
        )
        approval_id = context.approval_id
        if specification.approval_required:
            resolved = await self.service.approval_gate.runtime_approvals.resolve_valid_for(
                action,
                approval_id=approval_id,
            )
            if resolved is None:
                if approval_id is not None:
                    await self._run(
                        lambda: self.service._audit_spec(
                            "specification.stale-approval-rejected",
                            specification,
                            context.actor_ref,
                            metadata={"approval_id": approval_id},
                        ),
                        message="failed to persist stale governance approval audit",
                    )
                pending = await self.service.approval_gate.ensure_pending_approval_with_event(
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
            approval_id = resolved.approval_id
            await self._run(
                lambda: self.service._audit_spec(
                    "specification.approval-resolved",
                    specification,
                    context.actor_ref,
                    metadata={"approval_id": approval_id, "decision": "approved"},
                ),
                message="failed to persist governance approval resolution audit",
            )

        conversion = existing or TaskConversion(
            specification_id=specification.id,
            specification_revision=specification.revision,
            specification_digest=specification.content_digest,
            proposal_id=proposal_id,
            task_id=new_id("task"),
            approval_id=approval_id,
        )

        def reserve_current_conversion() -> TaskConversion:
            current = self.repository.get_specification(specification.id)
            if (
                current.revision != specification.revision
                or current.content_digest != specification.content_digest
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Specification changed before its Task conversion could be reserved",
                )
            return self.repository.reserve_conversion(conversion)

        # Current-spec validation and reservation share one Governance persistence offload.
        # Concurrent async revisions therefore cannot slip between the final validation and the
        # durable reservation and bind a Task to stale specification content.
        reserved = await self._run(
            reserve_current_conversion,
            message="failed to reserve governance Task conversion",
        )
        task_title = proposal.title if proposal is not None else specification.goal[:120]
        task_objective = specification.goal
        base_key = (
            f"governance.convert:{specification.id}:{specification.revision}:"
            f"{specification.content_digest}"
        )
        task = await self.service.kernel.create_task(
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
        task = await self.service.kernel.update_task(
            idempotency_key=f"{base_key}:provenance",
            task_id=reserved.task_id,
            metadata={
                "governance": self.service._task_governance_metadata(specification, approval_id)
            },
            actor_ref=context.actor_ref,
            source="proposal-specification-governance",
        )
        completed = await self._run(
            lambda: self.repository.complete_conversion(
                specification.id,
                approval_id=approval_id,
            ),
            message="failed to complete governance Task conversion",
        )
        if proposal_id is not None:
            await self._run(
                lambda: self.service._mark_proposal_converted(
                    proposal_id,
                    completed.task_id,
                ),
                message="failed to persist converted governance proposal",
            )
        await self._run(
            lambda: self.service._audit(
                "specification.converted-to-task",
                "conversion",
                specification.id,
                context.actor_ref,
                specification.project_id,
                revision=specification.revision,
                digest=specification.content_digest,
                metadata={"task_id": completed.task_id, "approval_id": approval_id},
            ),
            message="failed to persist governance conversion audit",
        )
        return task


__all__ = ["AsyncGovernanceRuntime"]
