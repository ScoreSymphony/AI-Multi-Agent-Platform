from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest

from ai_multi_agent_platform.domain import (
    APPROVAL_TRANSITIONS,
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    WORKER_JOB_TRANSITIONS,
    Agent,
    Approval,
    ApprovalStatus,
    Artifact,
    Capability,
    Event,
    ExternalRef,
    Goal,
    ModelAssignment,
    Node,
    OwnerRef,
    Plan,
    PolicyScope,
    Provenance,
    Result,
    Run,
    RunStatus,
    Step,
    Task,
    TaskStatus,
    Tool,
    ToolInvocation,
    Worker,
    WorkerJob,
    WorkerJobStatus,
    can_transition,
    new_id,
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
    waiting_task = task.transition_to(TaskStatus.WAITING)
    assert task.status is TaskStatus.RUNNING
    assert waiting_task.status is TaskStatus.WAITING
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
    job = WorkerJob(
        run_id=run.id,
        worker_id=worker.id,
        owner_ref=OWNER,
        correlation_id=run.correlation_id,
    )

    assert run.worker_id == worker.id
    assert job.run_id == run.id
    assert job.correlation_id == run.correlation_id
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


def test_canonical_identity_cannot_be_reassigned_after_creation() -> None:
    task = Task(title="Stable identity", owner_ref=OWNER)

    with pytest.raises(FrozenInstanceError):
        task.id = "backend-task-42"  # type: ignore[misc]


def test_direct_status_assignment_cannot_bypass_transition_rules() -> None:
    run = Run(
        subject_type="task",
        subject_id=new_id("task"),
        owner_ref=OWNER,
        correlation_id="corr-status",
    )

    with pytest.raises(FrozenInstanceError):
        run.status = RunStatus.SUCCEEDED  # type: ignore[misc]

    with pytest.raises(ValueError):
        run.transition_to(RunStatus.SUCCEEDED)

    starting = run.transition_to(RunStatus.STARTING)
    running = starting.transition_to(RunStatus.RUNNING)
    succeeded = running.transition_to(RunStatus.SUCCEEDED)

    assert run.status is RunStatus.QUEUED
    assert succeeded.status is RunStatus.SUCCEEDED
    assert running.started_at is not None
    assert succeeded.finished_at is not None


def test_event_payload_and_provenance_are_defensively_deep_frozen() -> None:
    task_id = new_id("task")
    source_payload = {"outcome": {"labels": ["initial"]}}
    source_details = {"inputs": {"files": ["input.txt"]}}
    provenance = Provenance(source="agent", details=source_details)
    event = Event(
        event_type="task.updated",
        subject_type="task",
        subject_id=task_id,
        correlation_id="corr-event",
        payload=source_payload,
        provenance=provenance,
    )

    source_payload["outcome"]["labels"].append("mutated")
    source_details["inputs"]["files"].append("other.txt")

    assert event.payload["outcome"]["labels"] == ("initial",)
    assert event.provenance is not None
    assert event.provenance.details["inputs"]["files"] == ("input.txt",)
    with pytest.raises(TypeError):
        event.payload["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        event.payload["outcome"]["other"] = "value"


def test_deep_freeze_copies_non_dict_mapping_implementations() -> None:
    backing = {"labels": ["initial"]}
    wrapped = MappingProxyType(backing)
    event = Event(
        event_type="task.updated",
        subject_type="task",
        subject_id=new_id("task"),
        correlation_id="corr-proxy",
        payload={"wrapped": wrapped},
    )

    backing["labels"].append("mutated")

    assert event.payload["wrapped"]["labels"] == ("initial",)


def test_non_event_domain_metadata_is_also_deeply_immutable() -> None:
    source_metadata = {"nested": {"items": ["initial"]}}
    task = Task(title="Immutable metadata", owner_ref=OWNER, metadata=source_metadata)

    source_metadata["nested"]["items"].append("mutated")

    assert task.metadata["nested"]["items"] == ("initial",)
    with pytest.raises(TypeError):
        task.metadata["new"] = "value"  # type: ignore[index]


def test_agent_exposes_provider_neutral_policy_requirements_hook() -> None:
    source_policy = {"approval": {"required": True}}
    agent = Agent(name="Policy-aware agent", owner_ref=OWNER, policy_requirements=source_policy)

    source_policy["approval"]["required"] = False

    assert agent.policy_requirements["approval"]["required"] is True
    with pytest.raises(TypeError):
        agent.policy_requirements["approval"]["required"] = False


def test_result_supports_structured_status_data() -> None:
    task_id = new_id("task")
    source_status = {"completion": {"state": "succeeded", "warnings": []}}
    result = Result(
        subject_type="task",
        subject_id=task_id,
        owner_ref=OWNER,
        outcome="completed",
        status_data=source_status,
    )

    source_status["completion"]["warnings"].append("late mutation")

    assert result.status_data["completion"]["state"] == "succeeded"
    assert result.status_data["completion"]["warnings"] == ()


def test_model_assignment_requires_canonical_subject_identity() -> None:
    with pytest.raises(ValueError):
        ModelAssignment(
            subject_type="task",
            subject_id="backend-job-42",
            owner_ref=OWNER,
            requirements={},
        )

    assignment = ModelAssignment(
        subject_type="task",
        subject_id=new_id("task"),
        owner_ref=OWNER,
        requirements={"context": "large"},
    )
    validate_id(assignment.subject_id, "task")


def test_model_assignment_supports_capability_and_policy_scopes() -> None:
    capability = Capability(name="large-context")
    policy_scope = PolicyScope(
        name="sensitive-work",
        owner_ref=OWNER,
        criteria={"approval_required": True},
    )

    capability_assignment = ModelAssignment(
        subject_type="capability",
        subject_id=capability.id,
        owner_ref=OWNER,
        requirements={"context_window": 128000},
    )
    policy_assignment = ModelAssignment(
        subject_type="policy",
        subject_id=policy_scope.id,
        owner_ref=OWNER,
        requirements={"local_only": True},
    )

    validate_id(capability_assignment.subject_id, "cap")
    validate_id(policy_assignment.subject_id, "policy_scope")

    with pytest.raises(ValueError):
        ModelAssignment(
            subject_type="policy",
            subject_id="backend-policy-default",
            owner_ref=OWNER,
            requirements={},
        )


def test_approval_can_target_one_canonical_tool_invocation() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    invocation = ToolInvocation(
        tool_id=tool.id,
        owner_ref=OWNER,
        correlation_id="corr-tool-call",
    )
    approval = Approval(
        subject_type="tool_invocation",
        subject_id=invocation.id,
        owner_ref=OWNER,
        reason="Approve this exact sensitive invocation",
    )

    validate_id(invocation.id, "tool_invocation")
    assert approval.subject_id == invocation.id
    assert approval.subject_id != tool.id

    with pytest.raises(ValueError):
        Approval(
            subject_type="tool_invocation",
            subject_id="provider-call-42",
            owner_ref=OWNER,
        )


def test_relationship_fields_reject_noncanonical_ids() -> None:
    with pytest.raises(ValueError):
        Goal(title="Bad project", owner_ref=OWNER, project_id="db-row-1")

    with pytest.raises(ValueError):
        Agent(name="Bad capabilities", owner_ref=OWNER, capability_ids=("mcp-cap-1",))

    with pytest.raises(ValueError):
        Tool(name="Bad tool", owner_ref=OWNER, capability_ids=("provider-cap-1",))

    with pytest.raises(ValueError):
        Approval(
            subject_type="task",
            subject_id="executor-job-99",
            owner_ref=OWNER,
        )

    with pytest.raises(ValueError):
        Event(
            event_type="task.updated",
            subject_type="task",
            subject_id="workflow-123",
            correlation_id="corr-invalid-event",
        )


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
