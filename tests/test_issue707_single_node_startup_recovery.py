from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.server import main as server_main
from ai_multi_agent_platform.deployment.startup_recovery import (
    STARTUP_RECOVERY_DIR,
    STARTUP_RECOVERY_REPORT,
    load_startup_recovery_report,
    reconcile_single_node_startup,
    require_blocked_startup_run,
)
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.upgrade.versioning import (
    JsonVersionStateStore,
    current_release_versions,
)


async def _prepare_orphaned_run(root: Path) -> tuple[str, str]:
    (root / "db").mkdir(parents=True)
    (root / "files").mkdir()
    (root / "workspaces").mkdir()

    original = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=SqliteKernelRepository(root / "db" / "kernel.sqlite3"),
    )
    task = await original.create_task(
        idempotency_key="issue707:create",
        title="Interrupted single-node work",
        objective="Remain running when the original process disappears",
        owner_type="service",
        owner_id="issue707-test",
    )
    await original.ready_task(idempotency_key="issue707:ready", task_id=task.task_id)
    run = await original.start_task(idempotency_key="issue707:start", task_id=task.task_id)
    assert run.status.value == "running"

    # Materialize the rest of the ordinary single-node durable stores around the same
    # canonical kernel database, then pin the data root to the currently executing release.
    build_single_node_deployment(SingleNodeConfig(data_dir=root, secure_cookie=False))
    JsonVersionStateStore.for_data_dir(root).write(current_release_versions())
    return task.task_id, run.run_id


def test_clean_startup_recovery_is_repeatable_and_ready(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "clean"
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )

        first = await reconcile_single_node_startup(data_dir=root, kernel=deployment.kernel)
        second = await reconcile_single_node_startup(data_dir=root, kernel=deployment.kernel)

        assert first.ready_for_service is True
        assert first.unresolved_run_ids == ()
        assert second.ready_for_service is True
        assert second.unresolved_run_ids == ()
        assert first.report_path == root.resolve() / STARTUP_RECOVERY_DIR / STARTUP_RECOVERY_REPORT

        payload = load_startup_recovery_report(root)
        assert payload is not None
        assert payload["recovery_kind"] == "ordinary_single_node_startup"
        assert payload["ready_for_service"] is True
        assert payload["unresolved_run_ids"] == []

    asyncio.run(scenario())


def test_restarted_single_node_blocks_orphaned_running_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "orphaned"
        task_id, run_id = await _prepare_orphaned_run(root)
        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )

        recovery = await reconcile_single_node_startup(data_dir=root, kernel=restarted.kernel)

        assert recovery.ready_for_service is False
        assert recovery.unresolved_run_ids == (run_id,)
        report = json.loads(recovery.report_path.read_text(encoding="utf-8"))
        assert report["tasks"][0]["task_id"] == task_id
        assert report["tasks"][0]["entries"][0]["run_id"] == run_id
        assert report["tasks"][0]["entries"][0]["before"] == "running"
        assert report["tasks"][0]["entries"][0]["after"] == "running"
        assert (
            report["tasks"][0]["entries"][0]["disposition"]
            == "orphaned_reconciliation_required"
        )

        # A second pass remains blocked rather than inventing a terminal outcome.
        repeated = await reconcile_single_node_startup(data_dir=root, kernel=restarted.kernel)
        assert repeated.ready_for_service is False
        assert repeated.unresolved_run_ids == (run_id,)

    asyncio.run(scenario())


def test_operator_can_resolve_only_exact_startup_blocker(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "operator"
    task_id, run_id = asyncio.run(_prepare_orphaned_run(root))
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")

    assert server_main(["recover-startup"]) == 3
    # The ordinary serve path must stop before uvicorn while canonical recovery is unresolved.
    assert server_main(["serve"]) == 3
    require_blocked_startup_run(root, task_id=task_id, run_id=run_id)

    assert (
        server_main(
            [
                "resolve-startup-run",
                "--task-id",
                task_id,
                "--run-id",
                run_id,
                "--resolution",
                "failed",
                "--reason",
                "original single-node execution process disappeared",
            ]
        )
        == 0
    )

    report = load_startup_recovery_report(root)
    assert report is not None
    assert report["ready_for_service"] is True
    assert report["unresolved_run_ids"] == []
    assert server_main(["recover-startup"]) == 0

    restarted = build_single_node_deployment(
        SingleNodeConfig(data_dir=root, secure_cookie=False)
    )
    recovered = asyncio.run(restarted.kernel.get_run(task_id, run_id))
    assert recovered.status.value == "failed"
    assert recovered.recovery_required is False
    assert recovered.output["recovery_kind"] == "ordinary_single_node_startup"


def test_startup_resolution_rejects_unlisted_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "unlisted"
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        await reconcile_single_node_startup(data_dir=root, kernel=deployment.kernel)

        with pytest.raises(RuntimeError, match="already ready for service"):
            require_blocked_startup_run(
                root,
                task_id="task_00000000000000000000000000000000",
                run_id="run_00000000000000000000000000000000",
            )

    asyncio.run(scenario())
