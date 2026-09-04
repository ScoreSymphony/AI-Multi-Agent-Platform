"""Post-restore recovery coordination for disaster-restored deployments."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.kernel import PlatformKernel, RecoveryDisposition, RecoveryReport

RESTORE_RECOVERY_MARKER_VERSION = 1
RESTORE_RECOVERY_DIR = "recovery"
RESTORE_RECOVERY_PENDING = "restore-pending.json"
RESTORE_RECOVERY_REPORT = "restore-report.json"

RestoreValidationHook = Callable[
    [tuple[RecoveryReport, ...], dict[str, Any]],
    Awaitable[tuple[str, ...]],
]


@dataclass(frozen=True, slots=True)
class PostRestoreRecoveryResult:
    """Outcome of one required post-restore reconciliation/readiness pass."""

    data_dir: Path
    reports: tuple[RecoveryReport, ...]
    unresolved_run_ids: tuple[str, ...]
    report_path: Path
    ready_for_service: bool = False
    validation_checks: tuple[str, ...] = ()

    @property
    def runs_checked(self) -> int:
        return sum(len(report.entries) for report in self.reports)


def write_restore_recovery_marker(data_dir: Path, manifest: dict[str, Any]) -> Path:
    """Mark a freshly restored data root as requiring recovery before normal serving."""

    root = data_dir.expanduser().resolve()
    recovery_dir = root / RESTORE_RECOVERY_DIR
    recovery_dir.mkdir(parents=True, exist_ok=True)
    marker = recovery_dir / RESTORE_RECOVERY_PENDING
    payload = {
        "marker_version": RESTORE_RECOVERY_MARKER_VERSION,
        "reason": "disaster_restore",
        "restored_at": datetime.now(UTC).isoformat(),
        "backup_created_at": manifest.get("created_at"),
        "source_platform": manifest.get("platform", {}),
        "schema_migration": manifest.get("schema_migration", {}),
        "included_components": manifest.get("included_components", []),
        "restore_policy": manifest.get("restore_policy", {}),
    }
    _atomic_json_write(marker, payload)
    return marker


async def reconcile_restored_single_node(
    *,
    data_dir: Path,
    kernel: PlatformKernel,
    validation: RestoreValidationHook | None = None,
    retry_blocked: bool = False,
) -> PostRestoreRecoveryResult | None:
    """Reconcile and optionally validate a disaster-restored single-node deployment.

    A normal first pass is driven by ``restore-pending.json``. The marker is removed only after a
    durable recovery report has been written. The report itself remains authoritative for whether
    normal serving is allowed. ``retry_blocked=True`` re-opens a prior non-ready report so the
    operator/server path can retry unresolved work or a previously skipped/failed readiness gate.

    If the validation hook raises, the pending marker (when present) is deliberately retained. If
    unresolved canonical Runs remain, the report is persisted with ``ready_for_service=false``;
    subsequent normal server startup retries that blocked report instead of silently serving.
    """

    root = data_dir.expanduser().resolve()
    recovery_dir = root / RESTORE_RECOVERY_DIR
    marker = recovery_dir / RESTORE_RECOVERY_PENDING
    report_path = recovery_dir / RESTORE_RECOVERY_REPORT

    marker_exists = marker.is_file()
    if marker_exists:
        restore_metadata = _load_marker(marker)
    elif retry_blocked:
        blocked_restore_metadata = _load_blocked_report_restore(report_path)
        if blocked_restore_metadata is None:
            return None
        restore_metadata = blocked_restore_metadata
    else:
        return None

    reports = await kernel.recover_all()
    unresolved = tuple(
        entry.run_id
        for report in reports
        for entry in report.entries
        if entry.disposition is RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED
    )

    validation_checks: tuple[str, ...] = ()
    ready_for_service = False
    if not unresolved and validation is not None:
        validation_checks = await validation(reports, restore_metadata)
        ready_for_service = True

    report_payload = {
        "report_version": 1,
        "completed_at": datetime.now(UTC).isoformat(),
        "restore": restore_metadata,
        "runs_checked": sum(len(report.entries) for report in reports),
        "unresolved_run_ids": list(unresolved),
        "ready_for_service": ready_for_service,
        "validation_checks": list(validation_checks),
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
    _atomic_json_write(report_path, report_payload)
    if marker_exists:
        marker.unlink()
    return PostRestoreRecoveryResult(
        data_dir=root,
        reports=reports,
        unresolved_run_ids=unresolved,
        report_path=report_path,
        ready_for_service=ready_for_service,
        validation_checks=validation_checks,
    )


def require_blocked_restore_run(data_dir: Path, *, task_id: str, run_id: str) -> None:
    """Require that a Run is explicitly blocked by the authoritative restore report.

    This is the safety boundary for offline operator resolution. It prevents the recovery CLI from
    terminalizing arbitrary live Runs merely because it has direct access to the restored kernel.
    """

    report_path = data_dir.expanduser().resolve() / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_REPORT
    payload = _load_report(report_path)
    if payload.get("ready_for_service") is True:
        raise RuntimeError("restored deployment is already ready for service")

    unresolved = payload.get("unresolved_run_ids")
    if not isinstance(unresolved, list) or any(not isinstance(item, str) for item in unresolved):
        raise RuntimeError("restore recovery report contains invalid unresolved_run_ids")
    if run_id not in unresolved:
        raise RuntimeError(f"run {run_id} is not listed as unresolved by restore recovery")

    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise RuntimeError("restore recovery report contains invalid task entries")
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
    raise RuntimeError(f"run {run_id} is not an orphaned restore-recovery Run for task {task_id}")


def _load_marker(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("restore recovery marker is unreadable or invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("restore recovery marker must be a JSON object")
    if payload.get("marker_version") != RESTORE_RECOVERY_MARKER_VERSION:
        raise RuntimeError("restore recovery marker version is incompatible")
    return payload


def _load_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("restore recovery report is missing; run recover-restore first")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("restore recovery report is unreadable or invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("report_version") != 1:
        raise RuntimeError("restore recovery report version is incompatible")
    return payload


def _load_blocked_report_restore(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = _load_report(path)
    if payload.get("ready_for_service") is True:
        return None
    restore = payload.get("restore")
    if not isinstance(restore, dict):
        raise RuntimeError("restore recovery report does not contain restore metadata")
    return restore


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, path)
