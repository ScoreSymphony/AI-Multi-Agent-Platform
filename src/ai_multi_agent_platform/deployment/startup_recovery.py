"""Ordinary single-node startup reconciliation for issue #707.

This module is intentionally separate from disaster-restore recovery. The normal
single-Control-Plane profile must reconcile durable canonical Runs after an
abnormal process exit even when no backup/restore operation occurred.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.kernel import PlatformKernel, RecoveryDisposition, RecoveryReport

STARTUP_RECOVERY_REPORT_VERSION = 1
STARTUP_RECOVERY_DIR = "recovery"
STARTUP_RECOVERY_REPORT = "startup-report.json"


@dataclass(frozen=True, slots=True)
class SingleNodeStartupRecoveryResult:
    """Outcome of one ordinary single-node startup reconciliation pass."""

    data_dir: Path
    reports: tuple[RecoveryReport, ...]
    unresolved_run_ids: tuple[str, ...]
    report_path: Path
    ready_for_service: bool

    @property
    def runs_checked(self) -> int:
        return sum(len(report.entries) for report in self.reports)


async def reconcile_single_node_startup(
    *,
    data_dir: Path,
    kernel: PlatformKernel,
) -> SingleNodeStartupRecoveryResult:
    """Reconcile canonical Run state before an ordinary single-node serve.

    The pass is safe to repeat because the kernel recovery path owns canonical
    idempotency/reconciliation. A running Run whose execution backend can no
    longer be found is never guessed into a terminal state: it remains marked as
    requiring reconciliation and blocks authoritative serving until an operator
    resolves the exact Run through the canonical kernel outcome path.
    """

    root = data_dir.expanduser().resolve()
    report_path = root / STARTUP_RECOVERY_DIR / STARTUP_RECOVERY_REPORT

    reports = await kernel.recover_all()
    unresolved = tuple(
        entry.run_id
        for report in reports
        for entry in report.entries
        if entry.disposition is RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED
    )
    ready_for_service = not unresolved

    payload: dict[str, Any] = {
        "report_version": STARTUP_RECOVERY_REPORT_VERSION,
        "recovery_kind": "ordinary_single_node_startup",
        "completed_at": datetime.now(UTC).isoformat(),
        "runs_checked": sum(len(report.entries) for report in reports),
        "unresolved_run_ids": list(unresolved),
        "ready_for_service": ready_for_service,
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
    )


def require_blocked_startup_run(data_dir: Path, *, task_id: str, run_id: str) -> None:
    """Require that an exact Run is blocked by the latest startup recovery report."""

    report_path = (
        data_dir.expanduser().resolve() / STARTUP_RECOVERY_DIR / STARTUP_RECOVERY_REPORT
    )
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
    raise RuntimeError(
        f"run {run_id} is not an orphaned startup-recovery Run for task {task_id}"
    )


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
    except (OSError, json.JSONDecodeError) as exc:
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
