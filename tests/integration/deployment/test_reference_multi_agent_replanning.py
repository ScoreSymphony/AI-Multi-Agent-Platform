from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    CapabilityConstraint,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelRequest,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.reference_multi_agent import (
    REFERENCE_MULTI_AGENT_CONSTRAINT,
)
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, StepStatus, TaskStatus
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.planning import ProposalStatus, ReplanPolicy
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeModelProvider

_PASSWORD = "correct horse battery staple for issue 889"
_MODEL_ID = "model-issue-889-reference-replanning"


class _ExecuteFailureProvider(FakeModelProvider):
    def __init__(self, *, failure_limit: int) -> None:
        super().__init__()
        self.failure_limit = failure_limit
        self.execute_failures = 0

    async def generate(self, request: ModelRequest):
        if (
            request.messages
            and "Produce the task result using the completed research" in request.messages[-1]
            and self.execute_failures < self.failure_limit
        ):
            self.calls.append(request)
            self.execute_failures += 1
            raise ContractError(ErrorCode.TRANSIENT_FAILURE, "injected canonical execute failure")
        return await super().generate(request)


def _profile(
    name: str,
    role: str,
    *,
    required_capability_id: str | None = None,
) -> AgentProfile:
    capabilities = AgentCapabilityPolicy()
    if required_capability_id is not None:
        capabilities = AgentCapabilityPolicy(
            constraints=(CapabilityConstraint(required_capability_id, required=True),),
        )
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(
                content=f"Act as the canonical {role} for the reference multi-agent task.",
                version="1",
            )
        ),
        capabilities=capabilities,
    )


def _principal(agent: AgentRevisionRef) -> str:
    return f"agent:{agent.agent_id}@{agent.revision}"


def _install_model(deployment: Any, provider: FakeModelProvider) -> None:
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id=_MODEL_ID,
            display_name="Issue 889 reference replanning model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=32_768, modalities=("text",)),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )


def _create_agents(
    deployment: Any,
    owner: OwnerRef,
    *,
    include_reviewer: bool = True,
    developer_required_capability: str | None = None,
) -> dict[str, AgentRevisionRef]:
    profiles = {
        "researcher": _profile("Issue 889 Research Agent", "researcher"),
        "developer": _profile(
            "Issue 889 Execution Agent",
            "developer",
            required_capability_id=developer_required_capability,
        ),
    }
    if include_reviewer:
        profiles["reviewer"] = _profile("Issue 889 Review Agent", "reviewer")
    refs: dict[str, AgentRevisionRef] = {}
    for role, profile in profiles.items():
        revision = deployment.agents.create_agent(profile, owner_ref=owner)
        ref = AgentRevisionRef(revision.agent_id, revision.revision)
        refs[role] = ref
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_principal(ref),
                actor_types=frozenset({ActorType.AGENT}),
                allowed_actions=frozenset(
                    {AuthorizationAction.READ, AuthorizationAction.RESULT_READ}
                ),
                resource_types=frozenset({ResourceType.ARTIFACT, ResourceType.GENERIC}),
            )
        )
    return refs


async def _ready_task(deployment: Any, owner_id: str, suffix: str):
    task = await deployment.kernel.create_task(
        idempotency_key=f"issue-889:{suffix}:create",
        title=f"Issue 889 {suffix}",
        objective="Research, execute, review, and verify the requested reference result.",
        owner_type="user",
        owner_id=owner_id,
    )
    await deployment.kernel.ready_task(
        idempotency_key=f"issue-889:{suffix}:ready",
        task_id=task.task_id,
    )
    return task


def _step_by_title(deployment: Any, plan_id: str, title: str):
    state = deployment.coordination_repository.get_plan(plan_id)
    matches = [step for step in state.steps if step.title == title]
    assert len(matches) == 1
    return matches[0]


async def _failed_execute_run(deployment: Any, task_id: str, plan_id: str):
    step = _step_by_title(deployment, plan_id, "Produce the requested result")
    record = deployment.coordination_repository.get_step_record(step.id)
    assert record.latest_run_id is not None
    run = await deployment.kernel.get_run(task_id, record.latest_run_id)
    assert step.status is StepStatus.FAILED
    assert run.status is RunStatus.FAILED
    return step, run


def test_failure_evidence_replans_to_new_revision_and_repairs_success(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        provider = _ExecuteFailureProvider(failure_limit=1)
        _install_model(deployment, provider)
        admin = deployment.bootstrap_admin("issue-889-replan-admin", _PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        refs = _create_agents(deployment, owner)
        task = await _ready_task(deployment, admin.user_id, "replan-success")

        initial = await deployment.planning.propose(
            task_id=task.task_id,
            idempotency_key="issue-889:replan-success:propose-1",
            task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )
        assert initial.status is ProposalStatus.VALIDATED
        activated_initial = await deployment.planning.activate(
            initial.proposal.proposal_id,
            idempotency_key="issue-889:replan-success:activate-1",
            actor=ActorIdentity(admin.user_id, ActorType.HUMAN),
        )
        assert activated_initial.activation_plan_id is not None
        first_plan_id = activated_initial.activation_plan_id
        first_task = await deployment.kernel.get_task(task.task_id)
        assert first_task.status is TaskStatus.FAILED
        research = _step_by_title(deployment, first_plan_id, "Gather authoritative evidence")
        approach = _step_by_title(
            deployment,
            first_plan_id,
            "Prepare an independent execution approach",
        )
        failed_step, failed_run = await _failed_execute_run(
            deployment,
            task.task_id,
            first_plan_id,
        )
        assert research.status is StepStatus.SUCCEEDED
        assert approach.status is StepStatus.SUCCEEDED

        replacement = await deployment.replanning.from_terminal_run(
            task_id=task.task_id,
            run_id=failed_run.run_id,
            task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )
        assert replacement.status is ProposalStatus.VALIDATED
        assert replacement.proposal.base_plan_id == first_plan_id
        assert replacement.proposal.plan_revision == initial.proposal.plan_revision + 1
        assert replacement.proposal.evidence_refs == (failed_run.run_id,)
        assert replacement.proposal.supersedes_proposal_id == initial.proposal.proposal_id
        execute_draft = next(
            step for step in replacement.proposal.steps if step.key == "execute"
        )
        assert set(execute_draft.reuse_step_ids) == {research.id, approach.id}
        assert failed_step.id not in execute_draft.reuse_step_ids

        activated_replacement = await deployment.planning.activate(
            replacement.proposal.proposal_id,
            idempotency_key="issue-889:replan-success:activate-2",
            actor=ActorIdentity(admin.user_id, ActorType.HUMAN),
        )
        assert activated_replacement.activation_plan_id is not None
        assert activated_replacement.activation_plan_id != first_plan_id
        repaired_task = await deployment.kernel.get_task(task.task_id)
        assert repaired_task.status is TaskStatus.SUCCEEDED
        assert repaired_task.plan_ref == activated_replacement.activation_plan_id

        planning_history = deployment.planning.history(task.task_id)
        assert len(planning_history) == 2
        assert planning_history[0].proposal.proposal_id == initial.proposal.proposal_id
        assert planning_history[1].proposal.proposal_id == replacement.proposal.proposal_id
        assert planning_history[1].proposal.base_plan_id == first_plan_id
        assert provider.execute_failures == 1

        second_state = deployment.coordination_repository.get_plan(
            activated_replacement.activation_plan_id
        )
        assert all(step.status is StepStatus.SUCCEEDED for step in second_state.steps)
        for draft in replacement.proposal.steps:
            assert draft.assignment is not None
            if draft.key == "research":
                expected = refs["researcher"]
            elif draft.key == "review":
                expected = refs["reviewer"]
            else:
                expected = refs["developer"]
            assert draft.assignment.agent_id == expected.agent_id
            assert draft.assignment.agent_revision == expected.revision

    asyncio.run(scenario())


def test_replan_budget_exhaustion_is_resource_exhausted_and_stops_progress(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        provider = _ExecuteFailureProvider(failure_limit=2)
        _install_model(deployment, provider)
        deployment.planning.replan_policy = ReplanPolicy(max_replans=1)
        admin = deployment.bootstrap_admin("issue-889-budget-admin", _PASSWORD)
        _create_agents(deployment, OwnerRef(type="user", id=admin.user_id))
        task = await _ready_task(deployment, admin.user_id, "replan-budget")

        initial = await deployment.planning.propose(
            task_id=task.task_id,
            idempotency_key="issue-889:replan-budget:propose-1",
            task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )
        activated_initial = await deployment.planning.activate(
            initial.proposal.proposal_id,
            idempotency_key="issue-889:replan-budget:activate-1",
            actor=ActorIdentity(admin.user_id, ActorType.HUMAN),
        )
        assert activated_initial.activation_plan_id is not None
        _, first_failed_run = await _failed_execute_run(
            deployment,
            task.task_id,
            activated_initial.activation_plan_id,
        )
        replacement = await deployment.replanning.from_terminal_run(
            task_id=task.task_id,
            run_id=first_failed_run.run_id,
            task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )
        activated_replacement = await deployment.planning.activate(
            replacement.proposal.proposal_id,
            idempotency_key="issue-889:replan-budget:activate-2",
            actor=ActorIdentity(admin.user_id, ActorType.HUMAN),
        )
        assert activated_replacement.activation_plan_id is not None
        _, second_failed_run = await _failed_execute_run(
            deployment,
            task.task_id,
            activated_replacement.activation_plan_id,
        )

        before_task = await deployment.kernel.get_task(task.task_id)
        before_history = deployment.planning.history(task.task_id)
        before_run_ids = before_task.run_ids
        before_step_ids = before_task.step_ids
        assert before_task.status is TaskStatus.FAILED
        assert len(before_history) == 2

        with pytest.raises(ContractError) as raised:
            await deployment.replanning.from_terminal_run(
                task_id=task.task_id,
                run_id=second_failed_run.run_id,
                task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
            )
        assert raised.value.code is ErrorCode.RESOURCE_EXHAUSTED
        assert raised.value.message == "bounded replanning budget exhausted"

        after_task = await deployment.kernel.get_task(task.task_id)
        assert after_task.status is TaskStatus.FAILED
        assert after_task.run_ids == before_run_ids
        assert after_task.step_ids == before_step_ids
        assert deployment.planning.history(task.task_id) == before_history
        assert provider.execute_failures == 2

    asyncio.run(scenario())


def test_explicit_reference_path_fails_closed_when_required_agent_role_is_missing(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        _install_model(deployment, FakeModelProvider())
        admin = deployment.bootstrap_admin("issue-889-agent-admin", _PASSWORD)
        _create_agents(
            deployment,
            OwnerRef(type="user", id=admin.user_id),
            include_reviewer=False,
        )
        task = await _ready_task(deployment, admin.user_id, "missing-agent")

        with pytest.raises(ContractError) as raised:
            await deployment.planning.propose(
                task_id=task.task_id,
                idempotency_key="issue-889:missing-agent:propose",
                task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
            )
        assert raised.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
        assert raised.value.details["missing_roles"] == "reviewer"
        unchanged = await deployment.kernel.get_task(task.task_id)
        assert unchanged.plan_ref is None
        assert unchanged.step_ids == ()
        assert unchanged.run_ids == ()

    asyncio.run(scenario())


def test_reference_planning_fails_closed_when_required_agent_capability_is_unavailable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        _install_model(deployment, FakeModelProvider())
        admin = deployment.bootstrap_admin("issue-889-capability-admin", _PASSWORD)
        _create_agents(
            deployment,
            OwnerRef(type="user", id=admin.user_id),
            developer_required_capability="cap.issue-889.required",
        )
        task = await _ready_task(deployment, admin.user_id, "missing-capability")

        proposal = await deployment.planning.propose(
            task_id=task.task_id,
            idempotency_key="issue-889:missing-capability:propose",
            task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )
        assert proposal.status is ProposalStatus.INVALID
        assert proposal.activation_plan_id is None
        assert any(
            "developer" in error or "required Agent capabilities" in error
            for error in proposal.validation.errors
        )
        unchanged = await deployment.kernel.get_task(task.task_id)
        assert unchanged.plan_ref is None
        assert unchanged.step_ids == ()
        assert unchanged.run_ids == ()

    asyncio.run(scenario())
