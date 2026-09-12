from __future__ import annotations

import pytest

from ai_multi_agent_platform.coding_batches import (
    CheckState,
    CodingBatchCoordinator,
    CodingWorkItem,
    CombinedValidationEvidence,
    IntegrationState,
    OverlapKind,
    RequiredCheck,
    VerificationEvidence,
    WorkstreamResult,
    WorkstreamState,
    coding_batch_resource,
    deterministic_branch_ref,
)

BASE = "a" * 40
TARGET_2 = "b" * 40


def _item(
    name: str,
    path: str | None,
    *,
    dependencies: tuple[str, ...] = (),
    scopes: tuple[str, ...] = (),
) -> CodingWorkItem:
    return CodingWorkItem(
        work_item_id=name,
        task_id=f"task-{name}",
        plan_id="plan-batch",
        step_id=f"step-{name}",
        dependencies=dependencies,
        affected_paths=() if path is None else (path,),
        semantic_scopes=scopes,
    )


def _coordinator(*items: CodingWorkItem, limit: int = 4) -> tuple[CodingBatchCoordinator, str]:
    coordinator = CodingBatchCoordinator()
    batch = coordinator.create_batch(
        request_key="request-872",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=items,
        concurrency_limit=limit,
    )
    return coordinator, batch.batch_id


def _accept(
    coordinator: CodingBatchCoordinator,
    batch_id: str,
    workstream_id: str,
    revision: str,
    *changed_paths: str,
) -> None:
    coordinator.materialize_workstream(
        batch_id,
        workstream_id,
        workspace_id=f"workspace-{workstream_id}",
        snapshot_id=f"snapshot-{workstream_id}",
        agent_revision="agent-revision-1",
        agent_run_id=f"agent-run-{workstream_id}",
    )
    coordinator.start_workstream(batch_id, workstream_id)
    coordinator.record_result(
        batch_id,
        workstream_id,
        WorkstreamResult(
            output_revision=revision,
            changed_paths=changed_paths,
            diff_digest=f"digest-{workstream_id}",
        ),
    )
    coordinator.record_verification(
        batch_id,
        workstream_id,
        VerificationEvidence(
            verification_id=f"verification-{workstream_id}",
            subject_revision=revision,
            passed=True,
        ),
    )
    coordinator.accept_workstream(batch_id, workstream_id)


def test_three_proven_independent_items_are_parallel_ready() -> None:
    coordinator, batch_id = _coordinator(
        _item("a", "src/alpha/a.py"),
        _item("b", "frontend/beta/b.ts"),
        _item("c", "docs/gamma.md"),
    )

    ready = coordinator.ready_workstreams(batch_id)

    assert tuple(item.id for item in ready) == ("a", "b", "c")
    assert all(
        decision.kind is OverlapKind.INDEPENDENT for decision in coordinator.get(batch_id).overlaps
    )


def test_dependency_serializes_until_exact_predecessor_is_accepted() -> None:
    coordinator, batch_id = _coordinator(
        _item("a", "src/alpha/a.py"),
        _item("b", "src/beta/b.py", dependencies=("a",)),
    )
    batch = coordinator.get(batch_id)
    assert batch.workstream("a").state is WorkstreamState.READY
    assert batch.workstream("b").state is WorkstreamState.BLOCKED

    _accept(coordinator, batch_id, "a", "1" * 40, "src/alpha/a.py")

    assert coordinator.get(batch_id).workstream("b").state is WorkstreamState.READY


def test_same_file_and_unknown_overlap_are_not_started_in_parallel() -> None:
    coordinator, batch_id = _coordinator(
        _item("a", "src/shared.py"),
        _item("b", "src/shared.py"),
        _item("c", None),
    )
    batch = coordinator.get(batch_id)

    decisions = {(d.left_work_item_id, d.right_work_item_id): d.kind for d in batch.overlaps}
    assert decisions[("a", "b")] is OverlapKind.TEXTUAL_CONFLICT
    assert decisions[("a", "c")] is OverlapKind.UNKNOWN
    assert tuple(item.id for item in coordinator.ready_workstreams(batch_id)) == ("a",)


def test_repeated_request_and_materialization_are_idempotent() -> None:
    item = _item("a", "src/alpha/a.py")
    coordinator, batch_id = _coordinator(item)
    replay = coordinator.create_batch(
        request_key="request-872",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(item,),
        concurrency_limit=4,
    )
    assert replay.batch_id == batch_id

    first = coordinator.materialize_workstream(
        batch_id,
        "a",
        workspace_id="workspace-a",
        snapshot_id="snapshot-a",
        agent_revision="agent-revision-1",
        agent_run_id="agent-run-a",
    )
    second = coordinator.materialize_workstream(
        batch_id,
        "a",
        workspace_id="workspace-a",
        snapshot_id="snapshot-a",
        agent_revision="agent-revision-1",
        agent_run_id="agent-run-a",
    )

    assert first == second
    assert first.provenance.branch_ref == deterministic_branch_ref(batch_id, "a")
    with pytest.raises(ValueError, match="conflicts with recorded canonical provenance"):
        coordinator.materialize_workstream(
            batch_id,
            "a",
            workspace_id="workspace-duplicate",
            snapshot_id="snapshot-a",
            agent_revision="agent-revision-1",
            agent_run_id="agent-run-a",
        )


def test_changed_commit_invalidates_old_verification_subject() -> None:
    coordinator, batch_id = _coordinator(_item("a", "src/alpha/a.py"))
    coordinator.materialize_workstream(
        batch_id,
        "a",
        workspace_id="workspace-a",
        snapshot_id="snapshot-a",
        agent_revision="agent-revision-1",
        agent_run_id="agent-run-a",
    )
    coordinator.start_workstream(batch_id, "a")
    coordinator.record_result(
        batch_id,
        "a",
        WorkstreamResult("1" * 40, ("src/alpha/a.py",), "digest-a1"),
    )

    with pytest.raises(ValueError, match="does not match current output revision"):
        coordinator.record_verification(
            batch_id,
            "a",
            VerificationEvidence("verification-old", "2" * 40, True),
        )


def test_individual_passes_require_fresh_combined_validation_and_authorization() -> None:
    coordinator, batch_id = _coordinator(
        _item("a", "src/alpha/a.py"),
        _item("b", "frontend/beta/b.ts"),
    )
    _accept(coordinator, batch_id, "a", "1" * 40, "src/alpha/a.py")
    _accept(coordinator, batch_id, "b", "2" * 40, "frontend/beta/b.ts")

    candidate = coordinator.build_integration_candidate(
        batch_id,
        current_target_revision=BASE,
    )
    assert candidate.state is IntegrationState.READY

    candidate = coordinator.record_integrated_revision(
        batch_id,
        candidate.integration_id,
        integrated_revision="3" * 40,
    )
    assert candidate.state is IntegrationState.VALIDATING

    blocked = coordinator.record_combined_validation(
        batch_id,
        candidate.integration_id,
        CombinedValidationEvidence(
            subject_revision="3" * 40,
            verification_id="verification-combined-1",
            tests_passed=True,
            required_checks=(RequiredCheck("test", "2" * 40, CheckState.PASS),),
        ),
    )
    assert blocked.state is IntegrationState.BLOCKED
    assert "stale revision" in blocked.blocker_reasons[0]


def test_combined_pass_still_cannot_bypass_canonical_authorization() -> None:
    coordinator, batch_id = _coordinator(_item("a", "src/alpha/a.py"))
    _accept(coordinator, batch_id, "a", "1" * 40, "src/alpha/a.py")
    candidate = coordinator.build_integration_candidate(
        batch_id,
        current_target_revision=BASE,
    )
    coordinator.record_integrated_revision(
        batch_id,
        candidate.integration_id,
        integrated_revision="3" * 40,
    )
    validated = coordinator.record_combined_validation(
        batch_id,
        candidate.integration_id,
        CombinedValidationEvidence(
            subject_revision="3" * 40,
            verification_id="verification-combined",
            tests_passed=True,
            required_checks=(RequiredCheck("test", "3" * 40, CheckState.PASS),),
        ),
    )
    assert validated.state is IntegrationState.VALIDATED

    with pytest.raises(PermissionError, match="AuthorizedCodingBatchIntegration"):
        coordinator.mark_merge_ready(batch_id, candidate.integration_id)
    assert (
        coordinator.get(batch_id).integration_candidate(candidate.integration_id).state
        is IntegrationState.VALIDATED
    )


def test_stale_target_revision_blocks_candidate_and_invalidates_old_assumptions() -> None:
    coordinator, batch_id = _coordinator(_item("a", "src/alpha/a.py"))
    _accept(coordinator, batch_id, "a", "1" * 40, "src/alpha/a.py")

    candidate = coordinator.build_integration_candidate(
        batch_id,
        current_target_revision=TARGET_2,
    )

    assert candidate.state is IntegrationState.BLOCKED
    assert candidate.stale_base is True
    assert BASE in candidate.blocker_reasons[0]
    assert TARGET_2 in candidate.blocker_reasons[0]


def test_semantic_overlap_is_blocked_even_when_git_paths_are_textually_clean() -> None:
    coordinator, batch_id = _coordinator(
        _item("a", "src/contracts/a.py", scopes=("runtime-contract",)),
        _item("b", "tests/contracts/test_b.py", scopes=("runtime-contract",)),
    )
    _accept(coordinator, batch_id, "a", "1" * 40, "src/contracts/a.py")
    assert coordinator.get(batch_id).workstream("b").state is WorkstreamState.READY
    _accept(coordinator, batch_id, "b", "2" * 40, "tests/contracts/test_b.py")

    candidate = coordinator.build_integration_candidate(
        batch_id,
        current_target_revision=BASE,
    )

    assert candidate.state is IntegrationState.BLOCKED
    assert candidate.conflicts[0].kind is OverlapKind.SEMANTIC_OVERLAP


def test_projection_keeps_workspace_identity_distinct_from_branch_metadata() -> None:
    coordinator, batch_id = _coordinator(_item("a", "src/alpha/a.py"))
    coordinator.materialize_workstream(
        batch_id,
        "a",
        workspace_id="workspace-a",
        snapshot_id="snapshot-a",
        agent_revision="agent-revision-1",
        agent_run_id="agent-run-a",
        branch_ref="feature/provider-local-ref",
    )

    resource = coding_batch_resource(coordinator.get(batch_id))
    workstream = resource["workstreams"][0]

    assert workstream["workspace_id"] == "workspace-a"
    assert workstream["snapshot_id"] == "snapshot-a"
    assert workstream["branch_ref"] == "feature/provider-local-ref"
    assert "path" not in workstream
