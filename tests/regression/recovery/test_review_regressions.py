from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ai_multi_agent_platform.deployment import server as server_module
from ai_multi_agent_platform.deployment.startup_recovery import (
    STARTUP_RECOVERY_DIR,
    STARTUP_RECOVERY_REPORT,
    SingleNodeStartupRecoveryResult,
    load_startup_recovery_report,
)
from ai_multi_agent_platform.upgrade.versioning import (
    JsonVersionStateStore,
    current_release_versions,
)


class _RecordingKernel:
    def __init__(self) -> None:
        self.outcomes: list[dict[str, Any]] = []

    async def record_run_outcome(self, **kwargs: Any) -> None:
        self.outcomes.append(dict(kwargs))


def test_resolve_startup_run_reconciles_again_before_terminalizing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "stale-report"
    report_path = root / STARTUP_RECOVERY_DIR / STARTUP_RECOVERY_REPORT
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "report_version": 1,
                "recovery_kind": "ordinary_single_node_startup",
                "ready_for_service": False,
                "unresolved_run_ids": ["run_stale"],
                "tasks": [
                    {
                        "task_id": "task_stale",
                        "entries": [
                            {
                                "run_id": "run_stale",
                                "before": "running",
                                "after": "running",
                                "disposition": "orphaned_reconciliation_required",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    JsonVersionStateStore.for_data_dir(root).write(current_release_versions())
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")

    kernel = _RecordingKernel()
    deployment = SimpleNamespace(kernel=kernel)
    startup_calls = 0

    async def no_restore_recovery(_deployment: object) -> None:
        return None

    async def refreshed_startup_recovery(_deployment: object) -> SingleNodeStartupRecoveryResult:
        nonlocal startup_calls
        startup_calls += 1
        return SingleNodeStartupRecoveryResult(
            data_dir=root,
            reports=(),
            unresolved_run_ids=(),
            report_path=report_path,
            ready_for_service=True,
        )

    monkeypatch.setattr(server_module, "_run_restore_recovery", no_restore_recovery)
    monkeypatch.setattr(server_module, "_run_startup_recovery", refreshed_startup_recovery)

    result = server_module.main(
        [
            "resolve-startup-run",
            "--task-id",
            "task_stale",
            "--run-id",
            "run_stale",
            "--resolution",
            "failed",
            "--reason",
            "operator inspected an earlier orphan report",
        ],
        deployment_builder=lambda _config: deployment,
    )

    assert result == 3
    assert startup_calls == 1
    assert kernel.outcomes == []
    assert "fresh reconciliation no longer reports unresolved runs" in capsys.readouterr().err


def test_invalid_utf8_startup_report_is_a_recovery_error(tmp_path: Path) -> None:
    root = tmp_path / "invalid-encoding"
    report_path = root / STARTUP_RECOVERY_DIR / STARTUP_RECOVERY_REPORT
    report_path.parent.mkdir(parents=True)
    report_path.write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(RuntimeError, match="unreadable or invalid JSON"):
        load_startup_recovery_report(root)
