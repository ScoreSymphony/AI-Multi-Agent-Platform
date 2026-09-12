from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.coding_batches import (
    CheckState,
    CodingBatchCoordinator,
    CodingWorkItem,
    CombinedValidationEvidence,
    IntegrationExecutionProvenance,
    RequiredCheck,
    SqliteCodingBatchStore,
    VerificationEvidence,
    WorkstreamResult,
    WorkstreamState,
)

BASE = "a" * 40
OUTPUT = "b" * 40
INTEGRATED = "c" * 40


def _coordinator(path: Path) -> CodingBatchCoordinator:
    return CodingBatchCoordinator(SqliteCodingBatchStore(path))


def test_sqlite_store_recovers_workstream_and_integration_state_across_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "coding-batches.sqlite3"
    coordinator = _coordinator(path)
    item = CodingWorkItem(
        work_item_id="a",
        task_id="task-a",
        plan_id="plan-a",
        step_id="step-a",
        affected_paths=("src/a.py",),
        metadata={"issue_ref": "repository-issue-872"},
    )
    batch = coordinator.create_batch(
        request_key="request-872-durable",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(item,),
    )
    coordinator.materialize_workstream(
        batch.batch_id,
        "a",
        workspace_id="workspace-a",
        snapshot_id="snapshot-a",
        agent_revision="agent-revision-a",
        agent_run_id="agent-run-a",
    )
    coordinator.start_workstream(batch.batch_id, "a")
    coordinator.record_result(
        batch.batch_id,
        "a",
        WorkstreamResult(
            OUTPUT,
            ("src/a.py",),
            "digest-a",
            artifact_ids=("artifact-a",),
        ),
    )

    restarted = _coordinator(path)
    produced = restarted.get(batch.batch_id).workstream("a")
    assert produced.state is WorkstreamState.PRODUCED
    assert produced.provenance.workspace_id == "workspace-a"
    assert produced.provenance.output_revision == OUTPUT
    assert produced.result is not None
    assert produced.result.artifact_ids == ("artifact-a",)
    assert produced.work_item.metadata["issue_ref"] == "repository-issue-872"

    restarted.record_verification(
        batch.batch_id,
        "a",
        VerificationEvidence("verification-a", OUTPUT, True, ("check-a",)),
    )
    restarted.accept_workstream(batch.batch_id, "a")
    candidate = restarted.build_integration_candidate(
        batch.batch_id,
        current_target_revision=BASE,
    )
    execution = IntegrationExecutionProvenance(
        task_id="task-integration",
        plan_id="plan-integration",
        plan_revision=2,
        step_id="step-integration",
        run_id="run-integration",
        agent_revision="integration-agent@3",
        agent_run_id="agent-run-integration",
        workspace_id="workspace-integration",
        snapshot_id="snapshot-integration",
        branch_ref="coding/integration-durable",
    )
    restarted.bind_integration_execution(
        batch.batch_id,
        candidate.integration_id,
        execution,
    )

    mid_restart = _coordinator(path)
    integrating = mid_restart.get(batch.batch_id).integration_candidate(candidate.integration_id)
    assert integrating.execution == execution

    mid_restart.record_integrated_revision(
        batch.batch_id,
        candidate.integration_id,
        integrated_revision=INTEGRATED,
    )
    mid_restart.record_combined_validation(
        batch.batch_id,
        candidate.integration_id,
        CombinedValidationEvidence(
            subject_revision=INTEGRATED,
            verification_id="verification-combined",
            tests_passed=True,
            required_checks=(
                RequiredCheck(
                    "local-ci",
                    INTEGRATED,
                    CheckState.PASS,
                    external_ref="local-check-1",
                ),
            ),
            evaluation_refs=("evaluation-1",),
        ),
    )

    recovered = _coordinator(path)
    recovered_batch = recovered.get(batch.batch_id)
    recovered_candidate = recovered_batch.integration_candidate(candidate.integration_id)
    assert recovered_candidate.execution == execution
    assert recovered_candidate.integrated_revision == INTEGRATED
    assert recovered_candidate.validation is not None
    assert recovered_candidate.validation.verification_id == "verification-combined"
    assert recovered_candidate.validation.required_checks[0].external_ref == "local-check-1"
    assert recovered_candidate.validation.evaluation_refs == ("evaluation-1",)

    replay = recovered.create_batch(
        request_key="request-872-durable",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(item,),
    )
    assert replay.batch_id == batch.batch_id
    assert replay.integration_candidates == recovered_batch.integration_candidates
