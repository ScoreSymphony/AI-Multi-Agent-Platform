from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRunStatus,
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
from ai_multi_agent_platform.domain import OwnerRef, StepStatus, TaskStatus
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.testing import FakeModelProvider

_PASSWORD = "correct horse battery staple for issue 889"
_MODEL_ID = "model-issue-889-parallel-failure"


class _OrderedRootFailureProvider(FakeModelProvider):
    def __init__(self, order: Literal["failure-first", "success-first"]) -> None:
        super().__init__(response_text="canonical approach output")
        self.order = order

    async def generate(self, request: ModelRequest):
        role_instruction = request.messages[0] if request.messages else ""
        if "researcher" in role_instruction:
            if self.order == "success-first":
                await asyncio.sleep(0.02)
            self.calls.append(request)
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "injected canonical research failure",
            )
        if "developer" in role_instruction and self.order == "failure-first":
            await asyncio.sleep(0.02)
        return await super().generate(request)


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(
                content=f"Act as the canonical {role} for the reference multi-agent task.",
                version="1",
            )
        ),
    )


def _install_model(deployment: Any, provider: FakeModelProvider) -> None:
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id=_MODEL_ID,
            display_name="Issue 889 parallel failure model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=32_768, modalities=("text",)),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )


def _create_agents(deployment: Any, owner: OwnerRef) -> None:
    for role, name in (
        ("researcher", "Issue 889 Failure Research Agent"),
        ("developer", "Issue 889 Failure Execution Agent"),
        ("reviewer", "Issue 889 Failure Review Agent"),
    ):
        deployment.agents.create_agent(_profile(name, role), owner_ref=owner)


async def _run_order(tmp_path: Path, order: Literal["failure-first", "success-first"]):
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / order, secure_cookie=False)
    )
    provider = _OrderedRootFailureProvider(order)
    _install_model(deployment, provider)
    admin = deployment.bootstrap_admin(f"issue-889-{order}", _PASSWORD)
    _create_agents(deployment, OwnerRef(type="user", id=admin.user_id))
    task = await deployment.kernel.create_task(
        idempotency_key=f"issue-889:{order}:create",
        title=f"Issue 889 deterministic failure {order}",
        objective="Fail one parallel root and keep the fan-in closed deterministically.",
        owner_type="user",
        owner_id=admin.user_id,
    )
    await deployment.kernel.ready_task(
        idempotency_key=f"issue-889:{order}:ready",
        task_id=task.task_id,
    )
    proposal = await deployment.planning.propose(
        task_id=task.task_id,
        idempotency_key=f"issue-889:{order}:propose",
        task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
    )
    activated = await deployment.planning.activate(
        proposal.proposal.proposal_id,
        idempotency_key=f"issue-889:{order}:activate",
        actor=ActorIdentity(admin.user_id, ActorType.HUMAN),
    )
    assert activated.activation_plan_id is not None

    terminal_task = await deployment.kernel.get_task(task.task_id)
    state = deployment.coordination_repository.get_plan(activated.activation_plan_id)
    by_title = {step.title: step for step in state.steps}
    research = by_title["Gather authoritative evidence"]
    approach = by_title["Prepare an independent execution approach"]
    execute = by_title["Produce the requested result"]
    review = by_title["Review the exact produced result"]

    assert terminal_task.status is TaskStatus.FAILED
    assert research.status is StepStatus.FAILED
    assert approach.status is StepStatus.SUCCEEDED
    assert execute.status in {StepStatus.CANCELLED, StepStatus.SKIPPED}
    assert review.status in {StepStatus.CANCELLED, StepStatus.SKIPPED}
    assert deployment.coordination_repository.get_step_record(execute.id).latest_run_id is None
    assert deployment.coordination_repository.get_step_record(review.id).latest_run_id is None

    research_record = deployment.coordination_repository.get_step_record(research.id)
    approach_record = deployment.coordination_repository.get_step_record(approach.id)
    assert research_record.latest_run_id is not None
    assert approach_record.latest_run_id is not None
    research_runs = deployment.agents.repository.list_agent_runs(research_record.latest_run_id)
    approach_runs = deployment.agents.repository.list_agent_runs(approach_record.latest_run_id)
    assert len(research_runs) == 1
    assert len(approach_runs) == 1
    assert research_runs[0].status is AgentRunStatus.FAILED
    assert approach_runs[0].status is AgentRunStatus.SUCCEEDED

    return (
        terminal_task.status,
        tuple((step.title, step.status) for step in state.steps),
        tuple(
            (
                step.title,
                deployment.coordination_repository.get_step_record(step.id).latest_run_id is not None,
            )
            for step in state.steps
        ),
    )


def test_parallel_failure_aggregation_is_independent_of_completion_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        failure_first = await _run_order(tmp_path, "failure-first")
        success_first = await _run_order(tmp_path, "success-first")
        assert failure_first == success_first

    asyncio.run(scenario())
