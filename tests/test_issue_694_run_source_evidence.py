from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, Run, RunStatus, Task, TaskStatus, new_id
from ai_multi_agent_platform.kernel.models import RunState, TaskState
from ai_multi_agent_platform.learning import (
    InMemoryLearningRepository,
    KernelRunFailureEvidenceResolver,
    LearningGatePlan,
    LearningQualityGate,
    LearningService,
    LearningSourceBridge,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
    PromotionRegistry,
    RunFailureSourceRef,
)
from ai_multi_agent_platform.security import (
    AuthorizationGate,
    LocalAuthorizationProvider,
    RiskClassification,
)

OWNER = OwnerRef(type="user", id="issue-694-run-owner")


class _KernelStub:
    def __init__(self, task: TaskState, runs: tuple[RunState, ...]) -> None:
        self.task = task
        self.runs = {run.run_id: run for run in runs}

    async def get_task(self, task_id: str) -> TaskState:
        if task_id != self.task.task_id:
            raise ContractError(ErrorCode.NOT_FOUND, "canonical Task not found")
        return self.task

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        if task_id != self.task.task_id or run_id not in self.runs:
            raise ContractError(ErrorCode.NOT_FOUND, "canonical Run not found")
        return self.runs[run_id]


def _task(project_id: str) -> TaskState:
    return TaskState(
        task=Task(
            title="Issue 694 Run evidence",
            owner_ref=OWNER,
            status=TaskStatus.RUNNING,
            project_id=project_id,
        ),
        revision=3,
    )


def _run(
    task: TaskState,
    *,
    status: RunStatus = RunStatus.FAILED,
    revision: int = 4,
) -> RunState:
    return RunState(
        run=Run(
            subject_type="task",
            subject_id=task.task_id,
            owner_ref=OWNER,
            correlation_id=task.task_id,
            status=status,
            project_id=task.task.project_id,
        ),
        revision=revision,
    )


def _learning() -> LearningService:
    return LearningService(
        InMemoryLearningRepository(),
        quality_gate=LearningQualityGate(),
        promotion_registry=PromotionRegistry(()),
        authorization_gate=AuthorizationGate(LocalAuthorizationProvider(())),
    )


def _target() -> LearningTarget:
    return LearningTarget(
        resource_type=LearningTargetType.AGENT,
        resource_id=new_id("agent"),
        revision=1,
    )


def _create_pattern(
    bridge: LearningSourceBridge,
    project_id: str,
    source_refs: tuple[RunFailureSourceRef, ...],
):
    return asyncio.run(
        bridge.from_run_failure_pattern(
            source_refs=source_refs,
            problem="Repeated canonical Run failure.",
            target=_target(),
            improvement_type="owner_revision",
            expected_benefit="Reduce repeated Run failures.",
            risk=RiskClassification.STANDARD,
            gate_plan=LearningGatePlan(
                policy_id="issue-694-run",
                policy_version=1,
                require_evaluation=True,
                evaluation_suite_refs=("issue-694@1",),
            ),
            creator_ref="user:issue-694",
            proposed_change={"description": "Use bounded Run handling."},
            project_id=project_id,
        )
    )


def test_run_failure_pattern_binds_two_canonical_failures() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    first = _run(task, revision=4)
    second = _run(task, revision=5)
    kernel = _KernelStub(task, (first, second))
    bridge = LearningSourceBridge(
        _learning(),
        run_failures=KernelRunFailureEvidenceResolver(kernel),
    )

    candidate, created = _create_pattern(
        bridge,
        project_id,
        (
            RunFailureSourceRef(task.task_id, first.run_id),
            RunFailureSourceRef(task.task_id, second.run_id),
        ),
    )

    assert created is True
    assert candidate.source_type is LearningSourceType.RUN_FAILURE_PATTERN
    assert {reference.resource_id for reference in candidate.source_refs} == {
        first.run_id,
        second.run_id,
    }
    assert {reference.revision for reference in candidate.source_refs} == {"4", "5"}
    assert all(
        reference.digest and reference.digest.startswith("sha256:")
        for reference in candidate.source_refs
    )
    assert any(
        reference.kind == "task"
        and reference.resource_id == task.task_id
        and reference.revision == str(task.revision)
        for reference in candidate.evidence_refs
    )


def test_duplicate_run_evidence_does_not_satisfy_pattern_minimum() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    failed = _run(task)
    kernel = _KernelStub(task, (failed,))
    bridge = LearningSourceBridge(
        _learning(),
        run_failures=KernelRunFailureEvidenceResolver(kernel),
    )
    reference = RunFailureSourceRef(task.task_id, failed.run_id)

    with pytest.raises(ContractError) as error:
        _create_pattern(bridge, project_id, (reference, reference))

    assert error.value.code is ErrorCode.INVALID_REQUEST


def test_missing_and_successful_run_evidence_fail_closed() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    succeeded = _run(task, status=RunStatus.SUCCEEDED)
    resolver = KernelRunFailureEvidenceResolver(_KernelStub(task, (succeeded,)))

    with pytest.raises(ContractError) as missing:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(task.task_id, new_id("run")),
                project_id=project_id,
            )
        )
    assert missing.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(ContractError) as not_failure:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(task.task_id, succeeded.run_id),
                project_id=project_id,
            )
        )
    assert not_failure.value.code is ErrorCode.CONFLICT


def test_run_revision_digest_and_project_scope_are_verified() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    failed = _run(task)
    resolver = KernelRunFailureEvidenceResolver(_KernelStub(task, (failed,)))

    with pytest.raises(ContractError) as stale:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(
                    task.task_id,
                    failed.run_id,
                    revision=failed.revision + 1,
                ),
                project_id=project_id,
            )
        )
    assert stale.value.code is ErrorCode.CONFLICT

    with pytest.raises(ContractError) as digest:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(
                    task.task_id,
                    failed.run_id,
                    digest="sha256:wrong",
                ),
                project_id=project_id,
            )
        )
    assert digest.value.code is ErrorCode.CONFLICT

    with pytest.raises(ContractError) as scope:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(task.task_id, failed.run_id),
                project_id=new_id("project"),
            )
        )
    assert scope.value.code is ErrorCode.FORBIDDEN


def test_mixed_valid_invalid_run_pattern_fails_as_a_whole() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    failed = _run(task)
    succeeded = _run(task, status=RunStatus.SUCCEEDED)
    bridge = LearningSourceBridge(
        _learning(),
        run_failures=KernelRunFailureEvidenceResolver(_KernelStub(task, (failed, succeeded))),
    )

    with pytest.raises(ContractError) as error:
        _create_pattern(
            bridge,
            project_id,
            (
                RunFailureSourceRef(task.task_id, failed.run_id),
                RunFailureSourceRef(task.task_id, succeeded.run_id),
            ),
        )

    assert error.value.code is ErrorCode.CONFLICT
