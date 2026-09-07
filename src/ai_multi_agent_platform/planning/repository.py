"""Proposal-state repository interfaces for autonomous planning."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .models import ProposalRecord, ProposalStatus


class PlanningRepository(Protocol):
    def create(self, record: ProposalRecord) -> ProposalRecord: ...

    def get(self, proposal_id: str) -> ProposalRecord: ...

    def save(self, record: ProposalRecord, *, expected_revision: int) -> ProposalRecord: ...

    def list_for_task(self, task_id: str) -> tuple[ProposalRecord, ...]: ...

    def get_by_idempotency(self, task_id: str, key: str) -> ProposalRecord | None: ...

    def get_by_trigger(self, task_id: str, fingerprint: str) -> ProposalRecord | None: ...

    def pending_activation(self, task_id: str) -> ProposalRecord | None: ...


class InMemoryPlanningRepository:
    """Optimistic-revision repository for the zero-config local/reference path."""

    def __init__(self) -> None:
        self._records: dict[str, ProposalRecord] = {}
        self._lock = threading.RLock()

    def create(self, record: ProposalRecord) -> ProposalRecord:
        with self._lock:
            if record.proposal.proposal_id in self._records:
                raise ContractError(ErrorCode.CONFLICT, "planning proposal already exists")
            duplicate = self.get_by_idempotency(record.proposal.task_id, record.idempotency_key)
            if duplicate is not None:
                raise ContractError(ErrorCode.CONFLICT, "planning idempotency key already exists")
            self._records[record.proposal.proposal_id] = record
            return record

    def get(self, proposal_id: str) -> ProposalRecord:
        with self._lock:
            try:
                return self._records[proposal_id]
            except KeyError as exc:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    f"planning proposal not found: {proposal_id}",
                ) from exc

    def save(self, record: ProposalRecord, *, expected_revision: int) -> ProposalRecord:
        with self._lock:
            current = self.get(record.proposal.proposal_id)
            if current.revision != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "planning proposal revision changed concurrently",
                    details={
                        "proposal_id": record.proposal.proposal_id,
                        "expected_revision": expected_revision,
                        "current_revision": current.revision,
                    },
                )
            if record.revision != expected_revision + 1:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "saved proposal record revision must increase exactly once",
                )
            self._records[record.proposal.proposal_id] = record
            return record

    def list_for_task(self, task_id: str) -> tuple[ProposalRecord, ...]:
        with self._lock:
            records = [
                record
                for record in self._records.values()
                if record.proposal.task_id == task_id
            ]
            records.sort(key=lambda item: (item.proposal.created_at, item.proposal.proposal_id))
            return tuple(records)

    def get_by_idempotency(self, task_id: str, key: str) -> ProposalRecord | None:
        with self._lock:
            for record in self._records.values():
                if record.proposal.task_id == task_id and record.idempotency_key == key:
                    return record
            return None

    def get_by_trigger(self, task_id: str, fingerprint: str) -> ProposalRecord | None:
        with self._lock:
            for record in self._records.values():
                if (
                    record.proposal.task_id == task_id
                    and record.trigger_fingerprint == fingerprint
                    and record.status is not ProposalStatus.REJECTED
                ):
                    return record
            return None

    def pending_activation(self, task_id: str) -> ProposalRecord | None:
        with self._lock:
            pending = [
                record
                for record in self._records.values()
                if record.proposal.task_id == task_id
                and record.status is ProposalStatus.ACTIVATING
            ]
            if not pending:
                return None
            pending.sort(key=lambda item: item.updated_at, reverse=True)
            return pending[0]


def advance_record(
    record: ProposalRecord,
    *,
    status: ProposalStatus | None = None,
    activation_plan_id: str | None = None,
    approval_id: str | None = None,
    failure_reason: str | None = None,
) -> ProposalRecord:
    """Return the next immutable proposal-state revision."""

    return replace(
        record,
        status=status or record.status,
        activation_plan_id=(
            activation_plan_id if activation_plan_id is not None else record.activation_plan_id
        ),
        approval_id=approval_id if approval_id is not None else record.approval_id,
        failure_reason=failure_reason if failure_reason is not None else record.failure_reason,
        revision=record.revision + 1,
        updated_at=datetime.now(UTC),
    )
