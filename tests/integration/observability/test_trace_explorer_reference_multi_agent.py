from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.accounting import AccountingService, InMemoryUsageStore
from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.control_plane import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.deployment import (
    SingleNodeConfig,
    SingleNodeDeployment,
    build_single_node_deployment,
)
from ai_multi_agent_platform.domain import OwnerRef, TaskStatus
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
)
from ai_multi_agent_platform.planning import ProposalStatus
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeModelProvider


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(
                content=f"Act as the canonical {role} for the #889 trace fixture.",
                version="1",
            )
        ),
    )


def _principal(agent: AgentRevisionRef) -> str:
    return f"agent:{agent.agent_id}@{agent.revision}"


def _install_local_model(deployment: SingleNodeDeployment) -> FakeModelProvider:
    provider = FakeModelProvider()
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id="model-issue-901-reference-trace",
            display_name="Issue 901 reference multi-agent trace model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(
                context_window=32_768,
                modalities=("text",),
            ),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )
    return provider


def test_reference_multi_agent_golden_path_renders_through_trace_explorer(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        accounting = AccountingService(InMemoryUsageStore())
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
            accounting_service=accounting,
        )
        model_provider = _install_local_model(deployment)
        admin = deployment.bootstrap_admin(
            "issue-901-trace-admin",
            "correct horse battery staple for issue 901",
        )
        owner = OwnerRef(type="user", id=admin.user_id)
        revisions = {
            "researcher": deployment.agents.create_agent(
                _profile("Issue 901 Research Agent", "researcher"),
                owner_ref=owner,
            ),
            "developer": deployment.agents.create_agent(
                _profile("Issue 901 Execution Agent", "developer"),
                owner_ref=owner,
            ),
            "reviewer": deployment.agents.create_agent(
                _profile("Issue 901 Review Agent", "reviewer"),
                owner_ref=owner,
            ),
        }
        agent_refs = {
            role: AgentRevisionRef(revision.agent_id, revision.revision)
            for role, revision in revisions.items()
        }
        for agent in agent_refs.values():
            deployment.authorization.register(
                LocalPrincipalPolicy(
                    principal_ref=_principal(agent),
                    actor_types=frozenset({ActorType.AGENT}),
                    allowed_actions=frozenset(
                        {
                            AuthorizationAction.READ,
                            AuthorizationAction.RESULT_READ,
                        }
                    ),
                    resource_types=frozenset(
                        {
                            ResourceType.ARTIFACT,
                            ResourceType.GENERIC,
                        }
                    ),
                )
            )

        task = await deployment.kernel.create_task(
            idempotency_key="issue-901:reference-trace:create",
            title="Render the reference multi-agent golden path",
            objective=(
                "Research the requested change, prepare an execution approach, produce the "
                "result, and review the exact produced result."
            ),
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.ready_task(
            idempotency_key="issue-901:reference-trace:ready",
            task_id=task.task_id,
        )
        proposal = await deployment.planning.propose(
            task_id=task.task_id,
            idempotency_key="issue-901:reference-trace:propose",
        )
        assert proposal.status is ProposalStatus.VALIDATED
        activated = await deployment.planning.activate(
            proposal.proposal.proposal_id,
            idempotency_key="issue-901:reference-trace:activate",
            actor=ActorIdentity(actor_id=admin.user_id, actor_type=ActorType.HUMAN),
        )
        assert activated.status is ProposalStatus.ACTIVATED
        assert activated.activation_plan_id is not None

        completed = await deployment.kernel.get_task(task.task_id)
        assert completed.status is TaskStatus.SUCCEEDED
        state = deployment.coordination_repository.get_plan(activated.activation_plan_id)
        assert len(state.steps) == 4
        assert len(model_provider.calls) == 4

        context = RequestContext(
            request_id="issue-901-reference-trace-read",
            correlation_id="issue-901-reference-trace-read",
            actor=ActorContext(
                principal_ref=admin.user_id,
                owner_type="user",
                owner_id=admin.user_id,
                actor_type=ActorType.HUMAN.value,
            ),
        )
        trace = await deployment.control_plane.task_trace(
            context,
            task.task_id,
            PageQuery(limit=200, sort="timestamp", direction="asc"),
        )

        assert trace["telemetry_state"] == "available"
        assert trace["missing_sources"] == []
        assert isinstance(trace["items"], list)
        items_by_id = {
            item["id"]: item
            for item in trace["items"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        next_cursor = trace["next_cursor"]
        while next_cursor is not None:
            assert isinstance(next_cursor, str)
            page = await deployment.control_plane.task_trace(
                context,
                task.task_id,
                PageQuery(
                    limit=200,
                    cursor=next_cursor,
                    sort="timestamp",
                    direction="asc",
                ),
            )
            assert isinstance(page["items"], list)
            for item in page["items"]:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    items_by_id[item["id"]] = item
            next_cursor = page["next_cursor"]
        items = list(items_by_id.values())
        assert len(items) >= len(state.steps)

        projected_step_ids = {
            item["context"].get("step_id")
            for item in items
            if isinstance(item, dict) and isinstance(item.get("context"), dict)
        }
        assert {step.id for step in state.steps}.issubset(projected_step_ids)

        projected_run_ids = {
            item["context"].get("run_id")
            for item in items
            if isinstance(item, dict) and isinstance(item.get("context"), dict)
        }
        assert set(completed.run_ids).intersection(projected_run_ids)

        agent_nodes = [
            item for item in items if isinstance(item, dict) and item.get("name") == "agent.run"
        ]
        model_nodes = [
            item
            for item in items
            if isinstance(item, dict) and item.get("name") == "model.runtime.generate"
        ]
        assert len(agent_nodes) == len(state.steps)
        assert len(model_nodes) == len(model_provider.calls)
        agent_node_ids = {item["id"] for item in agent_nodes}
        assert all(item.get("parent_id") in agent_node_ids for item in model_nodes)
        assert all(
            isinstance(item.get("context"), dict)
            and item["context"].get("model_config_id") == "model-issue-901-reference-trace"
            and item["context"].get("agent_id")
            in {revision.agent_id for revision in revisions.values()}
            for item in model_nodes
        )

        assert any(
            isinstance(item, dict)
            and any(
                isinstance(link, dict)
                and link.get("type") == "plan"
                and link.get("id") == activated.activation_plan_id
                for link in item.get("resources", [])
            )
            for item in items
        )
        assert any(
            isinstance(item, dict) and item.get("name") == "coordination.plan.registered"
            for item in items
        )

    asyncio.run(scenario())
