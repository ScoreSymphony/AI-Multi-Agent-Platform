from __future__ import annotations

from ai_multi_agent_platform.coding_batches import (
    CodingBatchCoordinator,
    CodingBatchTelemetry,
    CodingWorkItem,
)
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry, TelemetryOutcome


def test_coding_batch_telemetry_uses_canonical_ids_without_host_paths() -> None:
    coordinator = CodingBatchCoordinator()
    batch = coordinator.create_batch(
        request_key="issue-872-observability",
        repository_id="repo-platform",
        target_ref="main",
        base_revision="a" * 40,
        work_items=(
            CodingWorkItem(
                work_item_id="A",
                task_id="task-A",
                plan_id="plan-A",
                step_id="step-A",
                affected_paths=("src/a.py",),
            ),
        ),
    )
    workstream = coordinator.materialize_workstream(
        batch.batch_id,
        "A",
        workspace_id="workspace-A",
        snapshot_id="snapshot-A",
        agent_revision="agent-A@1",
        agent_run_id="agent-run-A",
        branch_ref="coding/A-safe-ref",
    )
    exporter = InMemoryExporter()
    telemetry = CodingBatchTelemetry(Telemetry(exporter))

    telemetry.workstream_dispatched(
        coordinator.get(batch.batch_id),
        workstream,
        run_id="run-A",
        agent_id="agent-A",
        plan_revision=3,
        attempt=1,
    )

    assert len(exporter.timeline) == 1
    entry = exporter.timeline[0]
    assert entry.event_name == "coding_batch.workstream.dispatched"
    assert entry.outcome is TelemetryOutcome.SUCCEEDED
    assert entry.context.task_id == "task-A"
    assert entry.context.step_id == "step-A"
    assert entry.context.run_id == "run-A"
    assert entry.context.workspace_id == "workspace-A"
    assert entry.context.correlation_id == batch.batch_id
    assert entry.attributes["repository_id"] == "repo-platform"
    assert entry.attributes["base_revision"] == "a" * 40
    assert entry.attributes["branch_ref"] == "coding/A-safe-ref"
    assert "path" not in entry.attributes
    assert "worktree" not in entry.attributes
    assert len(exporter.logs) == 1
