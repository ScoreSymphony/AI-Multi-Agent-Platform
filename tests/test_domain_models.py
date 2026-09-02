from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ai_multi_agent_platform.domain import (
    APPROVAL_TRANSITIONS,
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    WORKER_JOB_TRANSITIONS,
    Approval,
    ApprovalStatus,
    Artifact,
    ExternalRef,
    Node,
    OwnerRef,
    Plan,
    Run,
    RunStatus,
    Step,
    Task,
    TaskStatus,
    Worker,
    WorkerJob,
    WorkerJobStatus,
    can_transition,
    validate_id,
)

OWNER = OwnerRef(type="user", id="user-1")


def test_scenario_one_task_one_run_one_artifact() -> None:
    task = Task(title="Analyze input", owner_ref=OWNER)
    run = Run(
        subject_type="task",
        subject_id=task.id,
        owner_ref=OWNER,
        correlation_id="corr-1",
    )
    artifact = Artifact(name="analysis.json", owner_ref=OWNER)

    validate_id(task.id, "task")
    validate_id(run.id, "run")
    validate_id(artifact.id, "artifact")
    assert run.subject_id == task.id


def test_scenario_two_retry_creates_two_run_identities() -> None:
    task = Task(title="Retryable task", owner_ref=OWNER)
    first = Run(
        subject_type="task",
        subject_id=task.id,
        owner_ref=OWNER,
        correlation_id="corr-retry",
        attempt=1,
    )
    second = Run(
        subject_type="task",
        subject_id=task.id,
        owner_ref=OWNER,
        correlation_id="corr-retry",
        attempt=2,
    )

    assert first.id != second.id
    assert first.subject_id == second.subject_id == task.id


def test_scenario_three_plan_with_dependent_steps() -> None:
    task = Task(title="Planned task", owner_ref=OWNER)
    plan = Plan(task_id=task.id, owner_ref=OWNER, active=True)
    prepare = Step(plan_id=plan.id, title="Prepare", owner_ref=OWNER)
    execute = Step(
        plan_id=plan.id,
        title="Execute",
        owner_ref=OWNER,
        depends_on=(prepare.id,),
    )

    assert execute.depends_on == (prepare.id,)


def test_scenario_four_task_waiting_for_approval() -> None:
    task = Task(title="Sensitive task", owner_ref=OWNER, status=TaskStatus.RUNNING)
    approval = Approval(
        subject_type="task",
        subject_id=task.id,
        owner_ref=OWNER,
        reason="Human approval required",
    )

    assert can_transition(TaskStatus.RUNNING, TaskStatus.WAITING, TASK_TRANSITIONS)
    task.status = TaskStatus.WAITING
    assert task.status is TaskStatus.WAITING
    assert approval.status is ApprovalStatus.PENDING
    assert can_transition(
        ApprovalStatus.PENDING,
        ApprovalStatus.APPROVED,
        APPROVAL_TRANSITIONS,
    )


def test_scenario_five_remote_worker_execution() -> None:
    node = Node(name="remote-node", owner_ref=OWNER)
    worker = Worker(node_id=node.id, name="remote-worker", owner_ref=OWNER)
    task = Task(title="Remote task", owner_ref=OWNER)
    run = Run(
        subject_type="task",
        subject_id=task.id,
        owner_ref=OWNER,
        correlation_id="corr-remote",
        worker_id=worker.id,
    )
    job = WorkerJob(run_id=run.id, worker_id=worker.id, owner_ref=OWNER)

    assert run.worker_id == worker.id
    assert job.run_id == run.id
    assert can_transition(
        WorkerJobStatus.QUEUED,
        WorkerJobStatus.ASSIGNED,
        WORKER_JOB_TRANSITIONS,
    )


def test_scenario_six_external_backend_ids_remain_noncanonical() -> None:
    task = Task(
        title="Adapter mapped task",
        owner_ref=OWNER,
        external_refs=(
            ExternalRef(system="orchestrator", kind="run", value="backend-run-42"),
            ExternalRef(system="executor", kind="job", value="backend-job-99"),
        ),
    )
    original_id = task.id

    assert task.id == original_id
    assert task.external_refs[0].value == "backend-run-42"
    assert task.external_refs[1].value == "backend-job-99"
    assert not task.id.startswith("backend-")


def test_run_rejects_backend_identifier_as_canonical_subject() -> None:
    with pytest.raises(ValueError):
        Run(
            subject_type="task",
            subject_id="backend-task-123",
            owner_ref=OWNER,
            correlation_id="corr-invalid",
        )


def test_canonical_id_rejects_malformed_uuid_payload() -> None:
    with pytest.raises(ValueError):
        validate_id("task_------------------------------------", "task")


def test_lifecycle_terminal_run_has_no_outgoing_transition() -> None:
    assert RUN_TRANSITIONS[RunStatus.SUCCEEDED] == frozenset()


def test_domain_layer_has_no_vendor_framework_imports() -> None:
    domain_dir = Path(__file__).parents[1] / "src" / "ai_multi_agent_platform" / "domain"
    forbidden_roots = {"hermes", "forge", "temporal"}

    for path in domain_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0].lower() for alias in node.names}
                assert roots.isdisjoint(forbidden_roots)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0].lower()
                assert root not in forbidden_roots
