from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRunStatus,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import HealthStatus, ModelRequest, ModelResponse
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.reference_multi_agent import (
    REFERENCE_MULTI_AGENT_CONSTRAINT,
)
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, StepStatus, TaskStatus
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeModelProvider

_PASSWORD = "correct horse battery staple for issue 889"
_MODEL_ID = "model-issue-889-reference-cancellation"


class _BlockingParallelRootProvider(FakeModelProvider):
    def __init__(self) -> None:
        super().__init__(response_text="canonical reference agent output")
        self.both_roots_started = asyncio.Event()
        self.release_roots = asyncio.Event()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if len(self.calls) <= 2:
            if len(self.calls) == 2:
                self.both_roots_started.set()
            await self.release_roots.wait()
        return ModelResponse(
            request_id=request.request_id,
            text=self.response_text or "canonical reference agent output",
            model_ref=self.model_ref,
        )


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


def _principal(agent: AgentRevisionRef) -> str:
    return f"agent:{agent.agent_id}@{agent.revision}"


def _install_model(deployment: Any, provider: FakeModelProvider) -> None:
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id=_MODEL_ID,
            display_name="Issue 889 reference cancellation model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=32_768, modalities=("text",)),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )


def _create_agents(deployment: Any, owner: OwnerRef) -> dict[str, AgentRevisionRef]:
    refs: dict[str, AgentRevisionRef] = {}
    for role, name in (
        ("researcher", "Issue 889 Cancellation Research Agent"),
        ("developer", "Issue 889 Cancellation Execution Agent"),
        ("reviewer", "Issue 889 Cancellation Review Agent"),
    ):
        revision = deployment.agents.create_agent(_profile(name, role), owner_ref=owner)
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


def test_parallel_reference_runs_cancel_without_late_completion_resurrection(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        provider = _BlockingParallelRootProvider()
        _install_model(deployment, provider)
        admin = deployment.bootstrap_admin("issue-889-cancellation-admin", _PASSWORD)
        _create_agents(deployment, OwnerRef(type="user", id=admin.user_id))

        task = await deployment.kernel.create_task(
            idempotency_key="issue-889:cancellation:create",
            title="Cancel the reference multi-agent golden path",
            objective="Exercise cancellation while the parallel reference roots are active.",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.ready_task(
            idempotency_key="issue-889:cancellation:ready",
            task_id=task.task_id,
        )
        proposal = await deployment.planning.propose(
            task_id=task.task_id,
            idempotency_key="issue-889:cancellation:propose",
            task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )

        activation = asyncio.create_task(
            deployment.planning.activate(
                proposal.proposal.proposal_id,
                idempotency_key="issue-889:cancellation:activate",
                actor=ActorIdentity(admin.user_id, ActorType.HUMAN),
            )
        )
        await asyncio.wait_for(provider.both_roots_started.wait(), timeout=2.0)

        running_task = await deployment.kernel.get_task(task.task_id)
        assert running_task.plan_ref is not None
        plan_id = running_task.plan_ref
        state = deployment.coordination_repository.get_plan(plan_id)
        by_title = {step.title: step for step in state.steps}
        research = by_title["Gather authoritative evidence"]
        approach = by_title["Prepare an independent execution approach"]
        execute = by_title["Produce the requested result"]
        review = by_title["Review the exact produced result"]
        assert research.status is StepStatus.RUNNING
        assert approach.status is StepStatus.RUNNING
        assert execute.status is StepStatus.PENDING
        assert review.status is StepStatus.PENDING

        cancelled = await deployment.coordination.cancel_plan(
            plan_id,
            idempotency_key="issue-889:cancellation:cancel",
        )
        assert all(step.status is StepStatus.CANCELLED for step in cancelled.steps)
        cancelled_task = await deployment.kernel.get_task(task.task_id)
        assert cancelled_task.status is TaskStatus.CANCELLED

        root_run_ids: list[str] = []
        for step in (research, approach):
            record = deployment.coordination_repository.get_step_record(step.id)
            assert record.latest_run_id is not None
            root_run_ids.append(record.latest_run_id)
            run = await deployment.kernel.get_run(task.task_id, record.latest_run_id)
            assert run.status is RunStatus.CANCELLED
            agent_runs = deployment.agents.repository.list_agent_runs(record.latest_run_id)
            assert len(agent_runs) == 1
            assert agent_runs[0].status is AgentRunStatus.CANCELLED

        assert deployment.coordination_repository.get_step_record(execute.id).latest_run_id is None
        assert deployment.coordination_repository.get_step_record(review.id).latest_run_id is None

        provider.release_roots.set()
        activated = await asyncio.wait_for(activation, timeout=2.0)
        assert activated.activation_plan_id == plan_id
        await asyncio.sleep(0)

        terminal_task = await deployment.kernel.get_task(task.task_id)
        assert terminal_task.status is TaskStatus.CANCELLED
        for run_id in root_run_ids:
            run = await deployment.kernel.get_run(task.task_id, run_id)
            assert run.status is RunStatus.CANCELLED
            agent_run = deployment.agents.repository.list_agent_runs(run_id)[0]
            assert agent_run.status is AgentRunStatus.CANCELLED
        final_state = deployment.coordination_repository.get_plan(plan_id)
        assert all(step.status is StepStatus.CANCELLED for step in final_state.steps)
        assert deployment.coordination_repository.get_step_record(execute.id).latest_run_id is None
        assert deployment.coordination_repository.get_step_record(review.id).latest_run_id is None
        history = await deployment.kernel.history(task.task_id)
        assert "task.succeeded" not in [event.event_type for event in history]

    asyncio.run(scenario())
