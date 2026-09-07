"""Canonical runtime-evidence bridge for bounded autonomous replanning (#439).

The bridge deliberately resolves runtime facts from platform-owned stores before asking the
PlanningService for a replacement Plan. It never executes a Step, invokes a capability, changes a
Verification result, or dispatches a Worker.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.coordination import CoordinationPhase, StepCoordinationRecord
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.verification import VerificationOutcome
from ai_multi_agent_platform.verification.audit import (
    VerificationAuditEvent,
    VerificationAuditEventType,
)

from .models import PlanningTrigger, ProposalRecord
from .service import PlanningService

ReplanningEventSink = Callable[[str, dict[str, JsonValue]], Awaitable[None] | None]


class CoordinationEvidenceRepository(Protocol):
    """Minimum #384 state needed to prove retry exhaustion server-side."""

    def get_step_record(self, step_id: str) -> StepCoordinationRecord: ...


class ReplanningEvidenceBridge:
    """Translate canonical Run/Verification/coordinator evidence into one bounded replan request.

    Trigger identity is derived from canonical evidence, so retries after process failure are
    idempotent. The underlying PlanningService remains authoritative for proposal persistence,
    trigger de-duplication, validation and the configured replanning budget.
    """

    def __init__(
        self,
        planning: PlanningService,
        *,
        coordination_repository: CoordinationEvidenceRepository | None = None,
        event_sink: ReplanningEventSink | None = None,
    ) -> None:
        self._planning = planning
        self._coordination_repository = coordination_repository
        self._event_sink = event_sink

    async def from_terminal_run(
        self,
        *,
        task_id: str,
        run_id: str,
        workspace_id: str | None = None,
    ) -> ProposalRecord:
        """Request replanning from one canonical failed/timed-out Run."""

        run = await self._planning.kernel.get_run(task_id, run_id)
        if run.task_id != task_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "canonical Run does not belong to requested Task",
                details={"task_id": task_id, "run_id": run_id},
            )
        if run.status not in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
            raise ContractError(
                ErrorCode.CONFLICT,
                "terminal-failure replanning requires a failed or timed-out canonical Run",
                details={"run_id": run_id, "run_status": run.status.value},
            )
        reason = f"canonical Run {run_id} ended {run.status.value}"
        return await self._request(
            task_id=task_id,
            trigger=PlanningTrigger.TERMINAL_FAILURE,
            reason=reason,
            evidence_refs=(run_id,),
            workspace_id=workspace_id,
        )

    async def from_retry_exhaustion(
        self,
        *,
        task_id: str,
        step_id: str,
        workspace_id: str | None = None,
    ) -> ProposalRecord:
        """Request replanning only when #384 durably proves retry exhaustion for the Step."""

        if self._coordination_repository is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "retry-exhaustion replanning requires canonical coordination evidence",
            )
        record = self._coordination_repository.get_step_record(step_id)
        run_id = record.latest_run_id
        if record.task_id != task_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "coordination Step does not belong to requested Task",
                details={"task_id": task_id, "step_id": step_id},
            )
        if record.phase is not CoordinationPhase.TERMINAL or run_id is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "retry-exhaustion replanning requires terminal canonical Step coordination",
                details={"task_id": task_id, "step_id": step_id},
            )
        run = await self._planning.kernel.get_run(task_id, run_id)
        if run.status not in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
            raise ContractError(
                ErrorCode.CONFLICT,
                "retry-exhaustion evidence must reference a failed or timed-out canonical Run",
                details={"run_id": run_id, "run_status": run.status.value},
            )
        reason = f"canonical retry policy exhausted for Step {step_id}"
        return await self._request(
            task_id=task_id,
            trigger=PlanningTrigger.RETRY_EXHAUSTED,
            reason=reason,
            evidence_refs=(step_id, run_id),
            workspace_id=workspace_id,
        )

    async def from_verification(
        self,
        event: VerificationAuditEvent,
        *,
        workspace_id: str | None = None,
    ) -> ProposalRecord:
        """Request replanning from one canonical recorded Verification outcome."""

        if event.event_type is not VerificationAuditEventType.RESULT_RECORDED:
            raise ContractError(
                ErrorCode.CONFLICT,
                "replanning requires a canonical Verification result event",
                details={"verification_audit_event_id": event.event_id},
            )
        if event.task_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical Verification result is missing Task identity",
                details={"verification_audit_event_id": event.event_id},
            )
        trigger = {
            VerificationOutcome.NEEDS_CHANGES: PlanningTrigger.VERIFICATION_CHANGES_REQUIRED,
            VerificationOutcome.FAIL: PlanningTrigger.VERIFICATION_FAILED,
            VerificationOutcome.INCONCLUSIVE: PlanningTrigger.VERIFICATION_INCONCLUSIVE,
        }.get(event.outcome)
        if trigger is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Verification outcome does not justify replanning",
                details={
                    "verification_audit_event_id": event.event_id,
                    "outcome": None if event.outcome is None else event.outcome.value,
                },
            )
        reason = f"canonical Verification outcome {event.outcome.value}"
        evidence_refs = tuple(
            dict.fromkeys(
                value
                for value in (event.event_id, event.verification_id, event.run_id)
                if value is not None
            )
        )
        return await self._request(
            task_id=event.task_id,
            trigger=trigger,
            reason=reason,
            evidence_refs=evidence_refs,
            workspace_id=workspace_id,
        )

    async def from_task_constraint_change(
        self,
        *,
        task_id: str,
        evidence_ref: str,
        reason: str,
        workspace_id: str | None = None,
        task_constraints: tuple[str, ...] = (),
    ) -> ProposalRecord:
        """Request replanning after a caller identifies a canonical Task-change event.

        The Task state itself is re-read by PlanningService, so stale proposals remain rejected.
        ``evidence_ref`` must be a canonical event/change reference, not free-form authority.
        """

        if not evidence_ref.strip() or not reason.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Task-constraint replanning requires canonical evidence reference and reason",
            )
        return await self._request(
            task_id=task_id,
            trigger=PlanningTrigger.TASK_CONSTRAINT_CHANGED,
            reason=reason,
            evidence_refs=(evidence_ref,),
            workspace_id=workspace_id,
            task_constraints=task_constraints,
        )

    async def manual(
        self,
        *,
        task_id: str,
        evidence_ref: str,
        reason: str,
        workspace_id: str | None = None,
    ) -> ProposalRecord:
        """Explicit operator/manual replan using a stable canonical evidence/reference key."""

        if not evidence_ref.strip() or not reason.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "manual replanning requires evidence reference and reason",
            )
        return await self._request(
            task_id=task_id,
            trigger=PlanningTrigger.MANUAL,
            reason=reason,
            evidence_refs=(evidence_ref,),
            workspace_id=workspace_id,
        )

    async def _request(
        self,
        *,
        task_id: str,
        trigger: PlanningTrigger,
        reason: str,
        evidence_refs: tuple[str, ...],
        workspace_id: str | None,
        task_constraints: tuple[str, ...] = (),
    ) -> ProposalRecord:
        fingerprint = _evidence_fingerprint(
            task_id=task_id,
            trigger=trigger,
            reason=reason,
            evidence_refs=evidence_refs,
        )
        await self._emit(
            "planning.replan.triggered",
            task_id=task_id,
            trigger=trigger.value,
            reason=reason,
            evidence_refs=list(evidence_refs),
            trigger_fingerprint=fingerprint,
        )
        try:
            record = await self._planning.propose(
                task_id=task_id,
                idempotency_key=f"planning:evidence:{fingerprint}",
                trigger=trigger,
                reason=reason,
                workspace_id=workspace_id,
                evidence_refs=evidence_refs,
                task_constraints=task_constraints,
            )
        except ContractError as exc:
            event_type = (
                "planning.replan.exhausted"
                if exc.code is ErrorCode.RESOURCE_EXHAUSTED
                else "planning.replan.failed"
            )
            await self._emit(
                event_type,
                task_id=task_id,
                trigger=trigger.value,
                evidence_refs=list(evidence_refs),
                trigger_fingerprint=fingerprint,
                error_code=exc.code.value,
            )
            raise
        await self._emit(
            "planning.replan.proposed",
            task_id=task_id,
            proposal_id=record.proposal.proposal_id,
            trigger=trigger.value,
            evidence_refs=list(evidence_refs),
            trigger_fingerprint=fingerprint,
            status=record.status.value,
        )
        return record

    async def _emit(self, event_type: str, **attributes: JsonValue) -> None:
        if self._event_sink is None:
            return
        result = self._event_sink(event_type, dict(attributes))
        if result is not None:
            await result


def _evidence_fingerprint(
    *,
    task_id: str,
    trigger: PlanningTrigger,
    reason: str,
    evidence_refs: tuple[str, ...],
) -> str:
    payload = {
        "task_id": task_id,
        "trigger": trigger.value,
        "reason": reason,
        "evidence_refs": sorted(evidence_refs),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = ["CoordinationEvidenceRepository", "ReplanningEvidenceBridge", "ReplanningEventSink"]
