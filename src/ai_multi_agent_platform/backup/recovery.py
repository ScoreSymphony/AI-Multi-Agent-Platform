"""Post-restore recovery coordination for disaster-restored deployments."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.kernel import PlatformKernel, RecoveryDisposition, RecoveryReport

RESTORE_RECOVERY_MARKER_VERSION = 1
RESTORE_RECOVERY_DIR = "recovery"
RESTORE_RECOVERY_PENDING = "restore-pending.json"
RESTORE_RECOVERY_REPORT = "restore-report.json"


@dataclass(frozen=True, slots=True)
class PostRestoreRecoveryResult:
    """Outcome of one required post-restore kernel reconciliation pass."""

    data_dir: Path
    reports: tuple[RecoveryReport, ...]
    unresolved_run_ids: tuple[str, ...]
    report_path: Path

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
        "restore_policy": manifest.get("restore_policy", {}),
    }
    _atomic_json_write(marker, payload)
    return marker


async def reconcile_restored_single_node(
    *,
    data_dir: Path,
    kernel: PlatformKernel,
) -> PostRestoreRecoveryResult | None:
    """Reconcile canonical unfinished runs before a restored deployment starts serving.

    The pending marker is written by the restore service. If reconciliation fails, the marker is
    intentionally kept so a later retry cannot silently skip the required recovery pass. A
    successful pass may still leave canonical RUNNING runs with ``recovery_required=True``; that
    is the explicit kernel state for work whose former execution backend no longer exists.
    """

    root = data_dir.expanduser().resolve()
    recovery_dir = root / RESTORE_RECOVERY_DIR
    marker = recovery_dir / RESTORE_RECOVERY_PENDING
    if not marker.is_file():
        return None

    marker_payload = _load_marker(marker)
    reports = await kernel.recover_all()
    unresolved = tuple(
        entry.run_id
        for report in reports
        for entry in report.entries
        if entry.disposition is RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED
    )

    report_path = recovery_dir / RESTORE_RECOVERY_REPORT
    report_payload = {
        "report_version": 1,
        "completed_at": datetime.now(UTC).isoformat(),
        "restore": marker_payload,
        "runs_checked": sum(len(report.entries) for report in reports),
        "unresolved_run_ids": list(unresolved),
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
    marker.unlink()
    return PostRestoreRecoveryResult(
        data_dir=root,
        reports=reports,
        unresolved_run_ids=unresolved,
        report_path=report_path,
    )


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


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, path)
