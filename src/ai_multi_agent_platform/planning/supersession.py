"""Deterministic planning proposal supersession and activation concurrency for #439.

This layer keeps proposal concurrency inside the platform-owned planning boundary. It does not
create Runs, schedule Steps, dispatch Workers or replace the canonical Task/Plan lifecycle.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    OperationContext,
    PlatformEvent,
)
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.security import ActorIdentity, ProposedAction

from .activation import PlanningProposalActivation
from .handoff import PlanningActivationHandoff
from .inventory import PlanningInventoryBuilder
from .models import (
    PlannerOutput,
    PlanningInventory,
    PlanningRequest,
    PlanningTrigger,
    PlanProposal,
    PriorPlanSnapshot,
    ProposalRecord,
    ProposalStatus,
    ProposalValidation,
)
from .proposals import PlanningProposalFactory
from .replanning import PlanningReplanSupport
from .repository import advance_record
from .service import PlanningService as BasePlanningService
from .validation import PlanningProposalValidator

_SUPERSEDABLE_STATUSES = frozenset(
    {
        ProposalStatus.VALIDATED,
        ProposalStatus.AWAITING_APPROVAL,
        ProposalStatus.ACTIVATING,
    }
)
_PROPOSAL_VALIDATOR = PlanningProposalValidator()
_PROPOSAL_FACTORY = PlanningProposalFactory()


class PlanningService(BasePlanningService):
    """Planning service with focused internal responsibilities and deterministic supersession.

    Canonical Task/Plan mutation remains owned by the kernel. The additional per-Task lock closes
    the in-process check/use race between competing proposal activations. Once one proposal wins
    canonical activation, other proposals based on the exact same Task revision and base Plan
    become durably ``SUPERSEDED``.

    Replacement proposals also point to the durable proposal that activated their base Plan. This
    keeps proposal lineage explicit without mutating prior immutable proposal content.

    Inventory construction, deterministic proposal validation, immutable proposal construction,
    bounded replanning support, authorization/activation and canonical activation handoff are
    delegated to focused internal components. ``_inventory`` remains a compatibility seam because
    ``ReferencePlanningService`` intentionally layers trusted environment filtering on top of the
    canonical base inventory.
    """

    _activation_locks: dict[str, asyncio.Lock]

    def _inventory(self, task: TaskState, workspace_id: str | None) -> PlanningInventory:
        return PlanningInventoryBuilder(
            agents=self.agents,
            capabilities=self.capabilities,
            models=self.models,
        ).build(task, workspace_id)

    def validate(self, proposal: PlanProposal, request: PlanningRequest) -> ProposalValidation:
        return _PROPOSAL_VALIDATOR.validate(proposal, request)

    async def _prior_plan(self, task: TaskState) -> PriorPlanSnapshot | None:
        return await self._replan_support().prior_plan(task)

    def _enforce_replan_budget(self, task_id: str, trigger: PlanningTrigger) -> None:
        self._replan_support().enforce_budget(task_id, trigger)

    def _trigger_fingerprint(
        self,
        *,
        task: TaskState,
        trigger: PlanningTrigger,
        reason: str | None,
        evidence_refs: tuple[str, ...],
    ) -> str:
        return PlanningReplanSupport.trigger_fingerprint(
            task=task,
            trigger=trigger,
            reason=reason,
            evidence_refs=evidence_refs,
        )

    def _replan_support(self) -> PlanningReplanSupport:
        return PlanningReplanSupport(
            repository=self.repository,
            kernel=self.kernel,
            policy=self.replan_policy,
        )

    def _activation_handoff(self) -> PlanningActivationHandoff:
        return PlanningActivationHandoff(
            kernel=self.kernel,
            coordinator=self.coordinator,
        )

    def _activation_service(self) -> PlanningProposalActivation:
        return PlanningProposalActivation(
            repository=self.repository,
            kernel=self.kernel,
            authorization=self.authorization,
            coordinator=self.coordinator,
        )

    async def _activated_plan_event(self, proposal: PlanProposal) -> PlatformEvent | None:
        return await self._activation_handoff().activated_plan_event(proposal)

    @staticmethod
    def _plan_ref(event: PlatformEvent) -> str:
        return PlanningActivationHandoff.plan_ref(event)

    @staticmethod
    def _failed_replan_can_activate(proposal: PlanProposal, task: TaskState) -> bool:
        return PlanningActivationHandoff.failed_replan_can_activate(proposal, task)

    async def _ensure_handoff_ready(
        self,
        proposal: PlanProposal,
        event: PlatformEvent,
    ) -> None:
        await self._activation_handoff().ensure_ready(proposal, event, emit=self._emit)

    async def _handoff_to_coordinator(
        self,
        proposal: PlanProposal,
        event: PlatformEvent,
    ) -> None:
        await self._activation_handoff().handoff(proposal, event, emit=self._emit)

    def _activation_action(
        self,
        task: TaskState,
        record: ProposalRecord,
        actor: ActorIdentity,
    ) -> ProposedAction:
        return self._activation_service().activation_action(task, record, actor)

    def _operation_context(self, task: TaskState, key: str) -> OperationContext:
        return PlanningProposalActivation.operation_context(task, key)

    def _activation_lock(self, task_id: str) -> asyncio.Lock:
        locks = getattr(self, "_activation_locks", None)
        if locks is None:
            locks = {}
            self._activation_locks = locks
        lock = locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[task_id] = lock
        return lock

    def _proposal(self, request: PlanningRequest, output: PlannerOutput) -> PlanProposal:
        proposal = _PROPOSAL_FACTORY.build(request, output)
        prior = request.prior_plan
        if prior is None:
            return proposal

        predecessor = self._activated_proposal_for_plan(request.task_id, prior.plan_id)
        if predecessor is None:
            return proposal
        return replace(
            proposal,
            supersedes_proposal_id=predecessor.proposal.proposal_id,
        )

    async def activate(
        self,
        proposal_id: str,
        *,
        idempotency_key: str,
        actor: ActorIdentity | None = None,
        approval_id: str | None = None,
    ) -> ProposalRecord:
        initial = self.repository.get(proposal_id)
        async with self._activation_lock(initial.proposal.task_id):
            current = self.repository.get(proposal_id)
            if current.status is ProposalStatus.SUPERSEDED:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"proposal {proposal_id} is superseded",
                    details={"proposal_id": proposal_id},
                )
            try:
                activated = await self._activation_service().activate(
                    proposal_id,
                    idempotency_key=idempotency_key,
                    actor=actor,
                    approval_id=approval_id,
                    prior_plan=self._prior_plan,
                    emit=self._emit,
                )
            except ContractError as exc:
                if exc.code is ErrorCode.CONFLICT:
                    await self._supersede_if_stale(proposal_id)
                raise

            await self._supersede_competitors(activated)
            return activated

    async def _supersede_if_stale(self, proposal_id: str) -> None:
        """Persist staleness only when its authority can be proven safely.

        A Task revision change with the same canonical Plan is sufficient evidence that the
        proposal's immutable Task snapshot is obsolete. A canonical Plan change is different:
        ``plan.created`` alone is not proof that planning authorized the replacement. In that case
        we supersede only when this PlanningRepository contains the activated proposal that owns
        the Task's current Plan. This preserves the base service's activation-provenance guard.
        """

        record = self.repository.get(proposal_id)
        if record.status not in _SUPERSEDABLE_STATUSES:
            return
        proposal = record.proposal
        task = await self.kernel.get_task(proposal.task_id)
        revision_changed = task.revision != proposal.task_revision
        plan_changed = task.plan_ref != proposal.base_plan_id
        if not revision_changed and not plan_changed:
            return

        superseded_by_proposal_id: str | None = None
        if plan_changed:
            replacement = self._activated_proposal_for_plan(proposal.task_id, task.plan_ref)
            if replacement is None:
                return
            superseded_by_proposal_id = replacement.proposal.proposal_id

        await self._mark_superseded(
            record,
            superseded_by_proposal_id=superseded_by_proposal_id,
            reason="canonical Task/Plan state moved beyond the proposal base",
        )

    async def _supersede_competitors(self, winner: ProposalRecord) -> None:
        proposal = winner.proposal
        for record in self.repository.list_for_task(proposal.task_id):
            other = record.proposal
            if other.proposal_id == proposal.proposal_id:
                continue
            if record.status not in _SUPERSEDABLE_STATUSES:
                continue
            if (
                other.task_revision != proposal.task_revision
                or other.base_plan_id != proposal.base_plan_id
                or other.plan_revision != proposal.plan_revision
            ):
                continue
            await self._mark_superseded(
                record,
                superseded_by_proposal_id=proposal.proposal_id,
                reason="competing proposal lost canonical activation",
            )

    def _activated_proposal_for_plan(
        self,
        task_id: str,
        plan_id: str | None,
    ) -> ProposalRecord | None:
        if plan_id is None:
            return None
        candidates = [
            record
            for record in self.repository.list_for_task(task_id)
            if record.status is ProposalStatus.ACTIVATED and record.activation_plan_id == plan_id
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                item.proposal.created_at,
                item.updated_at,
                item.proposal.proposal_id,
            )
        )
        return candidates[-1]

    async def _mark_superseded(
        self,
        record: ProposalRecord,
        *,
        superseded_by_proposal_id: str | None,
        reason: str,
    ) -> None:
        current = self.repository.get(record.proposal.proposal_id)
        if current.status not in _SUPERSEDABLE_STATUSES:
            return
        updated = advance_record(
            current,
            status=ProposalStatus.SUPERSEDED,
            failure_reason=reason,
        )
        try:
            stored = self.repository.save(updated, expected_revision=current.revision)
        except ContractError as exc:
            if exc.code is ErrorCode.CONFLICT:
                return
            raise
        await self._emit(
            "planning.proposal.superseded",
            task_id=stored.proposal.task_id,
            proposal_id=stored.proposal.proposal_id,
            superseded_by_proposal_id=superseded_by_proposal_id,
            base_plan_id=stored.proposal.base_plan_id,
            reason=reason,
        )


__all__ = ["PlanningService"]
