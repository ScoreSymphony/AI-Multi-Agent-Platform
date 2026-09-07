"""Deterministic planning proposal supersession and activation concurrency for #439.

This layer keeps proposal concurrency inside the platform-owned planning boundary. It does not
create Runs, schedule Steps, dispatch Workers or replace the canonical Task/Plan lifecycle.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.security import ActorIdentity

from .models import (
    PlannerOutput,
    PlanningRequest,
    PlanProposal,
    ProposalRecord,
    ProposalStatus,
)
from .repository import advance_record
from .service import PlanningService as BasePlanningService

_SUPERSEDABLE_STATUSES = frozenset(
    {
        ProposalStatus.VALIDATED,
        ProposalStatus.AWAITING_APPROVAL,
        ProposalStatus.ACTIVATING,
    }
)


class PlanningService(BasePlanningService):
    """Planning service with deterministic same-Task activation and proposal lineage.

    Canonical Task/Plan mutation remains owned by the base planning/kernel path. The additional
    per-Task lock closes the in-process check/use race between competing proposal activations.
    Once one proposal wins canonical activation, other proposals based on the exact same Task
    revision and base Plan become durably ``SUPERSEDED``.

    Replacement proposals also point to the durable proposal that activated their base Plan. This
    keeps proposal lineage explicit without mutating prior immutable proposal content.
    """

    _activation_locks: dict[str, asyncio.Lock]

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
        proposal = super()._proposal(request, output)
        prior = request.prior_plan
        if prior is None:
            return proposal

        predecessors = [
            record
            for record in self.repository.list_for_task(request.task_id)
            if record.status is ProposalStatus.ACTIVATED
            and record.activation_plan_id == prior.plan_id
        ]
        if not predecessors:
            return proposal
        predecessors.sort(
            key=lambda item: (
                item.proposal.created_at,
                item.updated_at,
                item.proposal.proposal_id,
            )
        )
        return replace(
            proposal,
            supersedes_proposal_id=predecessors[-1].proposal.proposal_id,
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
                activated = await super().activate(
                    proposal_id,
                    idempotency_key=idempotency_key,
                    actor=actor,
                    approval_id=approval_id,
                )
            except ContractError as exc:
                if exc.code is ErrorCode.CONFLICT:
                    await self._supersede_if_stale(proposal_id)
                raise

            await self._supersede_competitors(activated)
            return activated

    async def _supersede_if_stale(self, proposal_id: str) -> None:
        record = self.repository.get(proposal_id)
        if record.status not in _SUPERSEDABLE_STATUSES:
            return
        task = await self.kernel.get_task(record.proposal.task_id)
        if (
            task.revision == record.proposal.task_revision
            and task.plan_ref == record.proposal.base_plan_id
        ):
            return
        await self._mark_superseded(
            record,
            superseded_by_proposal_id=None,
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
