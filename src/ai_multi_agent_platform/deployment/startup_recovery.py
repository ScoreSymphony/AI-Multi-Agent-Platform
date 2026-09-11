"""Ordinary single-node startup reconciliation for issues #707 and #758.

This module is intentionally separate from disaster-restore recovery. The normal
single-Control-Plane profile must reconcile durable canonical Runs and automatic
reviewer work after an abnormal process exit even when no backup/restore operation occurred.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.distributed import DispatchRecord, DispatchState
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel import (
    PlatformKernel,
    RecoveryDisposition,
    RecoveryEntry,
    RecoveryReport,
)
from ai_multi_agent_platform.verification.reviewer_recovery import ReviewerRecoveryRecord

STARTUP_RECOVERY_REPORT_VERSION = 1
STARTUP_RECOVERY_DIR = "recovery"
STARTUP_RECOVERY_REPORT = "startup-report.json"


class StartupCoordinator(Protocol):
    """Narrow #384 startup seam used by the deployment recovery gate."""

    async def reconcile_all(self) -> tuple[object, ...]: ...


class StartupDistributedRuntime(Protocol):
    """Narrow #14 startup seam used when distributed execution is enabled."""

    async def reconcile(self) -> tuple[object, ...]: ...


class StartupReviewerReconciler(Protocol):
    """Narrow #758 seam for durable automatic-reviewer reconciliation."""

    async def reconcile_startup(self) -> tuple[ReviewerRecoveryRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class SingleNodeStartupRecoveryResult:
    """Outcome of one ordinary single-node startup reconciliation pass."""

    data_dir: Path
    reports: tuple[RecoveryReport, ...]
    unresolved_run_ids: tuple[str, ...]
    report_path: Path
    ready_for_service: bool
    plans_reconciled: int = 0
    distributed_jobs_reconciled: int = 0
    reviewer_recoveries: tuple[ReviewerRecoveryRecord, ...] = ()
    blocked_verification_ids: tuple[str, ...] = ()

    @property
    def runs_checked(self) -> int:
        return sum(len(report.entries) for report in self.reports)


async def reconcile_single_node_startup(
    *,
    data_dir: Path,
    kernel: PlatformKernel,
    coordinator: StartupCoordinator | None = None,
    distributed_runtime: StartupDistributedRuntime | None = None,
    reviewer_reconciler: StartupReviewerReconciler | None = None,
) -> SingleNodeStartupRecoveryResult:
    """Reconcile durable runtime state before an ordinary single-node serve.

    Distributed Worker state is reconciled first so liveness/reservation truth is
    current before Run recovery. If that pass proves that an existing distributed
    dispatch for an active canonical Run is currently lost or cancellation-pending,
    startup records an explicit orphaned blocker instead of asking the kernel to
    redispatch a possibly accepted STARTING execution or aborting before a report can
    be written. Once those blockers are resolved, the durable Plan/Step coordinator
    resumes due waits/retries and the kernel scans every Task stream. Only after
    canonical Run ownership is stable may #758 reconcile automatic reviewer AgentRuns.
    The complete pass is safe to repeat.

    A running canonical Run whose execution backend can no longer be found is never
    guessed into a terminal state: it remains marked as requiring reconciliation and
    blocks authoritative serving until an operator resolves the exact Run through the
    canonical kernel outcome path. Automatic reviewer recovery likewise never invents
    a Verification outcome: it only reuses staged reviewer evidence or drives the
    existing bounded reviewer workflow after stale process-local ownership is reconciled.
    """

    root = data_dir.expanduser().resolve()
    report_path = root / STARTUP_RECOVERY_DIR / STARTUP_RECOVERY_REPORT

    distributed_jobs_reconciled = 0
    distributed_blockers: tuple[RecoveryReport, ...] = ()
    if distributed_runtime is not None:
        distributed_records = await distributed_runtime.reconcile()
        distributed_jobs_reconciled = len(distributed_records)
        distributed_blockers = await _distributed_recovery_blockers(kernel, distributed_records)

    plans_reconciled = 0
    if distributed_blockers:
        # Do not enter coordinator/kernel recovery while an already-owned distributed execution
        # is uncertain. In particular, a canonical STARTING Run may have been accepted remotely
        # before the Control Plane crashed, so treating UNAVAILABLE as NOT_FOUND would duplicate it.
        reports = distributed_blockers
    else:
        if coordinator is not None:
            plan_projections = await coordinator.reconcile_all()
            plans_reconciled = len(plan_projections)
        reports = await kernel.recover_all()

    unresolved = tuple(
        entry.run_id
        for report in reports
        for entry in report.entries
        if entry.disposition is RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED
    )

    reviewer_recoveries: tuple[ReviewerRecoveryRecord, ...] = ()
    if not unresolved and reviewer_reconciler is not None:
        reviewer_recoveries = await reviewer_reconciler.reconcile_startup()
    blocked_verification_ids = tuple(
        record.verification_id for record in reviewer_recoveries if record.blocked
    )
    ready_for_service = not unresolved and not blocked_verification_ids

    payload: dict[str, Any] = {
        "report_version": STARTUP_RECOVERY_REPORT_VERSION,
        "recovery_kind": "ordinary_single_node_startup",
        "completed_at": datetime.now(UTC).isoformat(),
        "runs_checked": sum(len(report.entries) for report in reports),
        "plans_reconciled": plans_reconciled,
        "distributed_jobs_reconciled": distributed_jobs_reconciled,
        "reviewer_recoveries_checked": len(reviewer_recoveries),
        "unresolved_run_ids": list(unresolved),
        "blocked_verification_ids": list(blocked_verification_ids),
        "ready_for_service": ready_for_service,
        "reviewer_recoveries": [
            {
                "verification_id": record.verification_id,
                "task_id": record.task_id,
                "disposition": record.disposition.value,
                "reviewer_agent_run_id": record.reviewer_agent_run_id,
                "replacement_agent_run_id": record.replacement_agent_run_id,
                "reason": record.reason,
            }
            for record in reviewer_recoveries
        ],
        "tasks": [
            {
                "task_id": report.task_id,
                "entries": [
                    {
                        "run_id": entry.run_id,
                        "before": entry.before.value,
                        "after": entry.after.value,
                        "disposition": entry.disposition.value,
                    }
                    for entry in report.entries
                ],
            }
            for report in reports
        ],
    }
    _atomic_json_write(report_path, payload)
    return SingleNodeStartupRecoveryResult(
        data_dir=root,
        reports=reports,
        unresolved_run_ids=unresolved,
        report_path=report_path,
        ready_for_service=ready_for_service,
        plans_reconciled=plans_reconciled,
        distributed_jobs_reconciled=distributed_jobs_reconciled,
        reviewer_recoveries=reviewer_recoveries,
        blocked_verification_ids=blocked_verification_ids,
    )


async def _distributed_recovery_blockers(
    kernel: PlatformKernel,
    records: tuple[object, ...],
) -> tuple[RecoveryReport, ...]:
    """Return active canonical Runs whose persisted distributed ownership is uncertain."""

    grouped: dict[str, list[RecoveryEntry]] = {}
    for candidate in records:
        if not isinstance(candidate, DispatchRecord):
            continue
        if candidate.state not in {DispatchState.LOST, DispatchState.CANCEL_PENDING}:
            continue
        run_id = candidate.job.execution.run_id
        task_id = candidate.job.execution.context.correlation_id
        # The distributed runtime can also own jobs that are not canonical kernel Runs. Only a
        # kernel-style Task correlation is eligible for the ordinary Run recovery gate.
        if not task_id.startswith("task_"):
            continue
        try:
            run = await kernel.get_run(task_id, run_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                continue
            raise
        if run.status not in {RunStatus.STARTING, RunStatus.RUNNING}:
            continue
        grouped.setdefault(task_id, []).append(
            RecoveryEntry(
                run_id=run_id,
                before=run.status,
                after=run.status,
                disposition=RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED,
            )
        )

    return tuple(
        RecoveryReport(
            task_id=task_id,
            entries=tuple(sorted(entries, key=lambda entry: entry.run_id)),
        )
        for task_id, entries in sorted(grouped.items())
    )


def require_blocked_startup_run(data_dir: Path, *, task_id: str, run_id: str) -> None:
    """Require that an exact Run is blocked by the latest startup recovery report."""

    report_path = data_dir.expanduser().resolve() / STARTUP_RECOVERY_DIR / STARTUP_RECOVERY_REPORT
    payload = _load_report(report_path)
    if payload.get("ready_for_service") is True:
        raise RuntimeError("single-node startup recovery is already ready for service")

    unresolved = payload.get("unresolved_run_ids")
    if not isinstance(unresolved, list) or any(not isinstance(item, str) for item in unresolved):
        raise RuntimeError("startup recovery report contains invalid unresolved_run_ids")
    if run_id not in unresolved:
        raise RuntimeError(f"run {run_id} is not listed as unresolved by startup recovery")

    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise RuntimeError("startup recovery report contains invalid task entries")
    for raw_task in tasks:
        if not isinstance(raw_task, dict) or raw_task.get("task_id") != task_id:
            continue
        entries = raw_task.get("entries")
        if not isinstance(entries, list):
            break
        for entry in entries:
            if (
                isinstance(entry, dict)
                and entry.get("run_id") == run_id
                and entry.get("disposition")
                == RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED.value
            ):
                return
        break
    raise RuntimeError(f"run {run_id} is not an orphaned startup-recovery Run for task {task_id}")


def load_startup_recovery_report(data_dir: Path) -> dict[str, Any] | None:
    """Load the latest startup report for diagnostics without mutating state."""

    path = data_dir.expanduser().resolve() / STARTUP_RECOVERY_DIR / STARTUP_RECOVERY_REPORT
    if not path.is_file():
        return None
    return _load_report(path)


def _load_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("startup recovery report is missing; run recover-startup first")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("startup recovery report is unreadable or invalid JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("report_version") != STARTUP_RECOVERY_REPORT_VERSION
        or payload.get("recovery_kind") != "ordinary_single_node_startup"
    ):
        raise RuntimeError("startup recovery report version or kind is incompatible")
    return payload


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    partial = path.with_name(f".{path.name}.partial")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, path)
    except OSError as exc:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"cannot persist startup recovery report: {path}") from exc
