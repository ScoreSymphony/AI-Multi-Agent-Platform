from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.reference_multi_agent import (
    REFERENCE_MULTI_AGENT_CONSTRAINT,
)
from ai_multi_agent_platform.domain import OwnerRef, StepStatus, TaskStatus
from ai_multi_agent_platform.execution.budgets.models import (
    BudgetConsumptionSource,
    BudgetDimension,
    TaskBudgetLimit,
    TaskBudgetPolicy,
)
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.planning import ProposalStatus
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeModelProvider

_PASSWORD = "correct horse battery staple for issue 902"
_MODEL_ID = "model-issue-902-reference-budget"


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(
                content=f"Act as the canonical {role} for the budgeted reference task.",
                version="1",
            )
        ),
    )


def _principal(agent: AgentRevisionRef) -> str:
    return f"agent:{agent.agent_id}@{agent.revision}"


def _create_reference_agents(deployment, owner: OwnerRef) -> dict[str, AgentRevisionRef]:
    refs: dict[str, AgentRevisionRef] = {}
    for role, name in (
        ("researcher", "Issue 902 Budget Research Agent"),
        ("developer", "Issue 902 Budget Execution Agent"),
        ("reviewer", "Issue 902 Budget Review Agent"),
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


def _install_local_model(deployment) -> FakeModelProvider:
    provider = FakeModelProvider(response_text="budgeted canonical output")
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id=_MODEL_ID,
            display_name="Issue 902 reference budget model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=32_768, modalities=("text",)),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )
    return provider


def test_reference_three_agent_path_stops_when_model_call_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        provider = _install_local_model(deployment)
        admin = deployment.bootstrap_admin("issue-902-budget-admin", _PASSWORD)
        refs = _create_reference_agents(deployment, OwnerRef(type="user", id=admin.user_id))

        task = await deployment.kernel.create_task(
            idempotency_key="issue-902:golden-budget:create",
            title="Reference multi-agent budget exhaustion",
            objective=(
                "Run the canonical research, execution and review flow until the platform-owned "
                "model-call budget is exhausted."
            ),
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.ready_task(
            idempotency_key="issue-902:golden-budget:ready",
            task_id=task.task_id,
        )
        canonical_task = await deployment.kernel.get_task(task.task_id)
        await deployment.task_budgets.put_policy(
            TaskBudgetPolicy(
                task_id=task.task_id,
                started_at=canonical_task.task.created_at,
                limits=(
                    TaskBudgetLimit(
                        dimension=BudgetDimension.MODEL_CALLS,
                        limit=2.0,
                        source=BudgetConsumptionSource.RUNTIME_COUNTER,
                    ),
                ),
                provenance={"acceptance_fixture": "#889/#902"},
            )
        )

        proposal = await deployment.planning.propose(
            task_id=task.task_id,
            idempotency_key="issue-902:golden-budget:propose",
            task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )
        assert proposal.status is ProposalStatus.VALIDATED
        activated = await deployment.planning.activate(
            proposal.proposal.proposal_id,
            idempotency_key="issue-902:golden-budget:activate",
            actor=ActorIdentity(admin.user_id, ActorType.HUMAN),
        )
        assert activated.activation_plan_id is not None

        completed = await deployment.kernel.get_task(task.task_id)
        assert completed.status is TaskStatus.FAILED
        state = deployment.coordination_repository.get_plan(activated.activation_plan_id)
        steps = {step.title: step for step in state.steps}
        assert steps["Gather authoritative evidence"].status is StepStatus.SUCCEEDED
        assert steps["Prepare an independent execution approach"].status is StepStatus.SUCCEEDED
        assert steps["Produce the requested result"].status is StepStatus.FAILED
        assert steps["Review the exact produced result"].status is not StepStatus.SUCCEEDED

        snapshot = await deployment.task_budgets.snapshot(task.task_id)
        model_calls = snapshot.for_dimension(BudgetDimension.MODEL_CALLS)
        assert model_calls is not None
        assert model_calls.consumed == 2.0
        assert model_calls.reserved == 0.0
        assert model_calls.remaining == 0.0
        assert model_calls.exhausted is True
        assert len(provider.calls) == 2

        ordinary_agent_runs = [
            run
            for run_id in completed.run_ids
            for run in deployment.agents.repository.list_agent_runs(run_id)
            if "verification_id" not in run.verification_context
        ]
        executed_agents = {run.agent for run in ordinary_agent_runs}
        assert refs["researcher"] in executed_agents
        assert refs["developer"] in executed_agents
        assert refs["reviewer"] not in executed_agents

    asyncio.run(scenario())
