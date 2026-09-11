"""Single-node restart reconciliation for automatic reviewer AgentRuns (#758).

Canonical Verification remains the review/completion authority. This module only
reconciles durable reviewer execution evidence after the previous Control Plane
process has disappeared; it does not infer review outcomes from AgentRun state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ai_multi_agent_platform.agents import AgentRunRecord, AgentRunStatus, AgentRuntime
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import TaskStatus

from .agent_workflow import AutomaticReviewerWorkflow, ReviewerRuntimeOptions
from .models import VerificationRequest, VerificationRequestStatus, VerifierKind
from .service import VerificationService

_REVIEW_CONTEXT_SCHEMA = "verification-reviewer-agent-v1"
_STAGED_DECISION_KEY = "automatic_reviewer_decision"
_RECOVERY_TELEMETRY_KEY = "automatic_reviewer_recovery"
_RECOVERY_TELEMETRY_SCHEMA = "automatic-reviewer-recovery-v1"
_RECOVERY_CAUSATION_ID = "automatic-reviewer-startup-recovery"
_RECOVERY_ABANDONED = "abandoned_after_process_restart"
_RECOVERY_TASK_CANCELLED = "cancelled_due_to_task"
_RECOVERY_VERIFICATION_TERMINAL = "cancelled_due_to_terminal_verification"
_RECOVERY_COMPLETED_STALE = "cancelled_after_verification_completed"


class ReviewerTaskState(Protocol):
    """Minimum canonical Task projection needed by reviewer recovery."""

    @property
    def status(self) -> TaskStatus: ...


class ReviewerTaskReader(Protocol):
    """Read canonical Task state without taking lifecycle authority."""

    async def get_task(self, task_id: str) -> ReviewerTaskState: ...


class ReviewerRecoveryDisposition(StrEnum):
    """Observable classification for one durable automatic-reviewer obligation."""

    ALREADY_COMPLETED = "already_completed"
    DISPATCHED = "dispatched"
    STAGED_DECISION_REUSED = "staged_decision_reused"
    ABANDONED_RETRIED = "abandoned_retried"
    RECONCILED = "reconciled"
    CANCELLED_STALE_RUN = "cancelled_stale_run"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ReviewerRecoveryRecord:
    """Content-safe startup evidence for one canonical Verification."""

    verification_id: str
    task_id: str
    disposition: ReviewerRecoveryDisposition
    reviewer_agent_run_id: str | None = None
    replacement_agent_run_id: str | None = None
    reason: str | None = None

    @property
    def blocked(self) -> bool:
        return self.disposition is ReviewerRecoveryDisposition.BLOCKED


class AutomaticReviewerStartupReconciler:
    """Recover automatic reviewer work after a single Control Plane process restart.

    This reconciler is intentionally single-node/process-restart scoped. During normal
    live operation ``AutomaticReviewerWorkflow`` continues to preserve an existing RUNNING
    reviewer instead of dispatching a duplicate. Startup, by contrast, runs before serving;
    therefore an unstaged RUNNING reviewer persisted by the previous process has lost its
    in-process execution owner and is explicitly terminalized before the bounded normal retry
    path is invoked.

    Completed NEEDS_CHANGES requests are still driven through the normal workflow so durable
    repair/reverification lineage can resume idempotently. Multi-process deployments need a
    durable lease/claim protocol and must not use this process-loss inference as cross-process
    liveness evidence.
    """

    def __init__(
        self,
        *,
        workflow: AutomaticReviewerWorkflow,
        agents: AgentRuntime,
        verification: VerificationService,
        tasks: ReviewerTaskReader | None = None,
    ) -> None:
        self._workflow = workflow
        self._agents = agents
        self._verification = verification
        self._tasks = tasks

    async def reconcile_startup(
        self,
        *,
        options: ReviewerRuntimeOptions | None = None,
    ) -> tuple[ReviewerRecoveryRecord, ...]:
        """Reconcile every durable Agent-verifier request in creation order."""

        snapshotter = getattr(self._verification, "snapshot_requests", None)
        if not callable(snapshotter):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer startup recovery requires durable Verification snapshots",
            )
        requests = snapshotter()
        if not isinstance(requests, tuple) or any(
            not isinstance(request, VerificationRequest) for request in requests
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "durable Verification snapshot returned malformed requests",
            )

        effective_options = options or ReviewerRuntimeOptions()
        recovered: list[ReviewerRecoveryRecord] = []
        for request in requests:
            if request.requested_verifier_kind is not VerifierKind.AGENT:
                continue
            recovered.append(
                await self._reconcile_request(
                    request,
                    options=effective_options,
                )
            )
        return tuple(recovered)

    async def _reconcile_request(
        self,
        request: VerificationRequest,
        *,
        options: ReviewerRuntimeOptions,
    ) -> ReviewerRecoveryRecord:
        binding_conflict = self._review_binding_conflict(request)
        if binding_conflict is not None:
            conflicting_run, reason = binding_conflict
            return ReviewerRecoveryRecord(
                verification_id=request.verification_id,
                task_id=request.task_id,
                disposition=ReviewerRecoveryDisposition.BLOCKED,
                reviewer_agent_run_id=conflicting_run.agent_run_id,
                reason=reason,
            )

        runs_before = self._review_runs_for(request.verification_id)
        all_before_ids = {
            run.agent_run_id for run in self._all_review_runs_for_task(request.task_id)
        }
        running = tuple(run for run in runs_before if run.status is AgentRunStatus.RUNNING)

        task_cancelled = await self._task_cancelled(request)
        if isinstance(task_cancelled, ReviewerRecoveryRecord):
            return task_cancelled
        if task_cancelled:
            if request.status is VerificationRequestStatus.PENDING:
                request = self._verification.cancel_request(
                    request.verification_id,
                    causation_id=_RECOVERY_CAUSATION_ID,
                )
            for run in running:
                self._terminalize_stale_run(
                    run,
                    status=AgentRunStatus.CANCELLED,
                    recovery_disposition=_RECOVERY_TASK_CANCELLED,
                    reason=(
                        "automatic reviewer startup recovery cancelled stale execution because "
                        "canonical Task is cancelled"
                    ),
                )
            if len(running) > 1:
                return ReviewerRecoveryRecord(
                    verification_id=request.verification_id,
                    task_id=request.task_id,
                    disposition=ReviewerRecoveryDisposition.BLOCKED,
                    reviewer_agent_run_id=_latest_run_id(running),
                    reason=(
                        "cancelled Task had multiple running reviewer AgentRuns; stale executions "
                        "were cancelled but the conflicting history requires operator review"
                    ),
                )
            return ReviewerRecoveryRecord(
                verification_id=request.verification_id,
                task_id=request.task_id,
                disposition=ReviewerRecoveryDisposition.CANCELLED_STALE_RUN,
                reviewer_agent_run_id=_latest_run_id(running),
                reason="canonical Task is cancelled",
            )

        if len(running) > 1:
            return ReviewerRecoveryRecord(
                verification_id=request.verification_id,
                task_id=request.task_id,
                disposition=ReviewerRecoveryDisposition.BLOCKED,
                reason="verification maps to multiple running reviewer AgentRuns",
            )

        if request.status in {
            VerificationRequestStatus.CANCELLED,
            VerificationRequestStatus.EXPIRED,
        }:
            cancelled_run_id = None
            if running:
                cancelled = self._terminalize_stale_run(
                    running[0],
                    status=AgentRunStatus.CANCELLED,
                    recovery_disposition=_RECOVERY_VERIFICATION_TERMINAL,
                    reason=(
                        "automatic reviewer startup recovery cancelled stale execution for "
                        f"{request.status.value} Verification"
                    ),
                )
                cancelled_run_id = cancelled.agent_run_id
            return ReviewerRecoveryRecord(
                verification_id=request.verification_id,
                task_id=request.task_id,
                disposition=ReviewerRecoveryDisposition.CANCELLED_STALE_RUN,
                reviewer_agent_run_id=cancelled_run_id,
                reason=f"verification is {request.status.value}",
            )

        if request.status not in {
            VerificationRequestStatus.PENDING,
            VerificationRequestStatus.COMPLETED,
        }:
            return ReviewerRecoveryRecord(
                verification_id=request.verification_id,
                task_id=request.task_id,
                disposition=ReviewerRecoveryDisposition.BLOCKED,
                reviewer_agent_run_id=_latest_run_id(runs_before),
                reason=f"unsupported Verification status during recovery: {request.status.value}",
            )

        completed_before = request.status is VerificationRequestStatus.COMPLETED
        staged_before = any(
            _has_staged_decision(run)
            for run in runs_before
            if run.status in {AgentRunStatus.RUNNING, AgentRunStatus.SUCCEEDED}
        )
        stale_completed_run_id: str | None = None
        abandoned_run_id: str | None = None
        if completed_before and running:
            stale = self._terminalize_stale_run(
                running[0],
                status=AgentRunStatus.CANCELLED,
                recovery_disposition=_RECOVERY_COMPLETED_STALE,
                reason=(
                    "automatic reviewer startup recovery cancelled stale execution because "
                    "canonical Verification is already completed"
                ),
            )
            stale_completed_run_id = stale.agent_run_id
        elif running and not staged_before:
            abandoned = self._terminalize_stale_run(
                running[0],
                status=AgentRunStatus.FAILED,
                recovery_disposition=_RECOVERY_ABANDONED,
                reason=(
                    "automatic reviewer execution owner disappeared during Control Plane "
                    "restart; bounded retry required"
                ),
            )
            abandoned_run_id = abandoned.agent_run_id

        try:
            result = await self._workflow.run_request(
                request.verification_id,
                options=options,
            )
        except Exception as exc:  # noqa: BLE001 - startup must persist an explicit blocker
            return ReviewerRecoveryRecord(
                verification_id=request.verification_id,
                task_id=request.task_id,
                disposition=ReviewerRecoveryDisposition.BLOCKED,
                reviewer_agent_run_id=(
                    abandoned_run_id or stale_completed_run_id or _latest_run_id(runs_before)
                ),
                reason=f"{type(exc).__name__}: {exc}",
            )

        runs_after = self._review_runs_for(request.verification_id)
        all_runs_after = self._all_review_runs_for_task(request.task_id)
        replacement = next(
            (run.agent_run_id for run in all_runs_after if run.agent_run_id not in all_before_ids),
            None,
        )
        latest = result.latest.reviewer_run
        resumed_descendant = result.latest.request.verification_id != request.verification_id

        if abandoned_run_id is not None:
            disposition = ReviewerRecoveryDisposition.ABANDONED_RETRIED
            reason = (
                "prior single-node reviewer execution ownership disappeared; stale AgentRun was "
                "terminalized before one bounded retry"
            )
        elif staged_before and not completed_before:
            disposition = ReviewerRecoveryDisposition.STAGED_DECISION_REUSED
            reason = "reused durable staged reviewer decision without another reviewer execution"
        elif not runs_before and runs_after:
            disposition = ReviewerRecoveryDisposition.DISPATCHED
            reason = "pending Verification had no reviewer AgentRun; dispatched one bounded attempt"
        elif completed_before and replacement is None and not resumed_descendant:
            disposition = ReviewerRecoveryDisposition.ALREADY_COMPLETED
            reason = "canonical Verification is already completed; no reviewer dispatch required"
        else:
            disposition = ReviewerRecoveryDisposition.RECONCILED
            reason = "existing canonical reviewer or repair lineage reconciled idempotently"

        return ReviewerRecoveryRecord(
            verification_id=request.verification_id,
            task_id=request.task_id,
            disposition=disposition,
            reviewer_agent_run_id=(
                abandoned_run_id
                or stale_completed_run_id
                or (None if latest is None else latest.agent_run_id)
            ),
            replacement_agent_run_id=replacement,
            reason=reason,
        )

    async def _task_cancelled(
        self,
        request: VerificationRequest,
    ) -> bool | ReviewerRecoveryRecord:
        if self._tasks is None:
            return False
        try:
            task = await self._tasks.get_task(request.task_id)
        except Exception as exc:  # noqa: BLE001 - missing/corrupt Task must fail closed
            return ReviewerRecoveryRecord(
                verification_id=request.verification_id,
                task_id=request.task_id,
                disposition=ReviewerRecoveryDisposition.BLOCKED,
                reviewer_agent_run_id=_latest_run_id(
                    self._review_runs_for(request.verification_id)
                ),
                reason=f"cannot resolve canonical Task during reviewer recovery: {exc}",
            )
        return task.status is TaskStatus.CANCELLED

    def _terminalize_stale_run(
        self,
        run: AgentRunRecord,
        *,
        status: AgentRunStatus,
        recovery_disposition: str,
        reason: str,
    ) -> AgentRunRecord:
        current = self._agents.service.repository.get_agent_run(run.agent_run_id)
        if current.status is not AgentRunStatus.RUNNING:
            return current
        telemetry = dict(current.telemetry)
        telemetry[_RECOVERY_TELEMETRY_KEY] = {
            "schema": _RECOVERY_TELEMETRY_SCHEMA,
            "disposition": recovery_disposition,
            "reason": reason,
            "terminal_status": status.value,
            "verification_id": current.verification_context.get("verification_id"),
        }
        return self._agents.finish_agent_run(
            current.agent_run_id,
            status=status,
            error=reason,
            telemetry=telemetry,
        )

    def _review_binding_conflict(
        self,
        request: VerificationRequest,
    ) -> tuple[AgentRunRecord, str] | None:
        """Reject AgentRuns that claim this Verification without its exact durable binding."""

        for record in self._agents.service.repository.list_agent_runs():
            context = record.verification_context
            if context.get("verification_id") != request.verification_id:
                continue
            if context.get("schema") != _REVIEW_CONTEXT_SCHEMA:
                return (
                    record,
                    "reviewer AgentRun claims Verification with an unexpected binding schema",
                )
            read_only = context.get("read_only")
            if not isinstance(read_only, bool):
                return (
                    record,
                    "reviewer AgentRun claims Verification with malformed read_only binding",
                )
            expected = {
                "schema": _REVIEW_CONTEXT_SCHEMA,
                "verification_id": request.verification_id,
                "task_id": request.task_id,
                "policy_id": request.policy_id,
                "policy_version": request.policy_version,
                "stage_id": request.stage_id,
                "subject": {
                    "type": request.subject.subject_type,
                    "id": request.subject.subject_id,
                    "revision": request.subject.revision,
                    "digest": request.subject.digest,
                },
                "repair_attempt": request.repair_attempt,
                "correlation_id": request.correlation_id,
                "agent_id": record.agent.agent_id,
                "agent_revision": record.agent.revision,
                "model_config_id": record.selected_model_config_id,
                "provider_id": record.selected_provider_id,
                "read_only": read_only,
                "capability_ids": list(record.capability_ids),
                "capability_versions": dict(record.capability_versions),
            }
            if record.task_id != request.task_id or dict(context) != expected:
                return (
                    record,
                    "reviewer AgentRun no longer matches its exact canonical Verification binding",
                )
        return None

    def _review_runs_for(self, verification_id: str) -> tuple[AgentRunRecord, ...]:
        return tuple(
            record
            for record in self._agents.service.repository.list_agent_runs()
            if record.verification_context.get("schema") == _REVIEW_CONTEXT_SCHEMA
            and record.verification_context.get("verification_id") == verification_id
        )

    def _all_review_runs_for_task(self, task_id: str) -> tuple[AgentRunRecord, ...]:
        return tuple(
            record
            for record in self._agents.service.repository.list_agent_runs()
            if record.task_id == task_id
            and record.verification_context.get("schema") == _REVIEW_CONTEXT_SCHEMA
        )


def _has_staged_decision(run: AgentRunRecord) -> bool:
    """Return whether durable reviewer output exists without interpreting its outcome."""

    return _STAGED_DECISION_KEY in run.telemetry


def _latest_run_id(runs: tuple[AgentRunRecord, ...]) -> str | None:
    return None if not runs else runs[-1].agent_run_id


__all__ = [
    "AutomaticReviewerStartupReconciler",
    "ReviewerRecoveryDisposition",
    "ReviewerRecoveryRecord",
    "ReviewerTaskReader",
]
