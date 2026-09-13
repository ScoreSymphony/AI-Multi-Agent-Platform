from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    InstructionSource,
)
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.testing import FakeModelProvider


def test_single_node_repairs_crash_after_agent_run_before_context_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "issue-650-crash", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        provider = FakeModelProvider()
        deployment.models.register_provider(provider)
        model_id = "model-issue-650-crash"
        deployment.models.register_model(
            ModelConfiguration(
                config_id=model_id,
                display_name="Issue 650 crash model",
                provider_id=provider.descriptor.provider_id,
                capabilities=ModelCapabilities(context_window=65_536, modalities=("text",)),
                location=ModelLocation.LOCAL,
                health=HealthStatus.HEALTHY,
            )
        )
        admin = deployment.bootstrap_admin("issue650-crash-admin", "issue-650-test-password")
        project = deployment.scopes.create_project(
            key="issue-650-crash-project",
            name="Issue 650 crash project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        agent = deployment.agents.create_agent(
            AgentProfile(
                name="Issue 650 crash worker",
                role="worker",
                instructions=AgentInstructions(
                    role=InstructionSource(
                        content="Use the canonical Context Bundle.",
                        version="issue-650-v1",
                    )
                ),
                model=AgentModelPolicy(allow_task_override=True),
            ),
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project.id,
        )
        task = await deployment.kernel.create_task(
            idempotency_key="issue650-crash-create",
            title="Crash binding recovery",
            objective="Persist AgentRun before the Context binding write fails.",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        await deployment.kernel.update_task(
            idempotency_key="issue650-crash-bind-agent",
            task_id=task.task_id,
            metadata=encode_agent_execution_binding(
                AgentExecutionBinding(
                    agent_id=agent.agent_id,
                    agent_revision=agent.revision,
                    model_config_id=model_id,
                )
            ),
        )
        await deployment.kernel.ready_task(
            idempotency_key="issue650-crash-ready",
            task_id=task.task_id,
        )

        original_put = deployment.context.run_bindings.put

        def fail_binding_write(_binding: object) -> object:
            raise RuntimeError("simulated crash before ContextRunBinding persistence")

        monkeypatch.setattr(deployment.context.run_bindings, "put", fail_binding_write)
        with pytest.raises(RuntimeError, match="ContextRunBinding persistence"):
            await deployment.kernel.start_task(
                idempotency_key="issue650-crash-start",
                task_id=task.task_id,
                actor_ref=admin.user_id,
            )
        monkeypatch.setattr(deployment.context.run_bindings, "put", original_put)

        agent_runs = deployment.agents.repository.list_agent_runs()
        assert len(agent_runs) == 1
        agent_run = agent_runs[0]
        bundles = deployment.context.bundles.list_for_run(agent_run.run_id)
        assert len(bundles) == 1
        bundle = bundles[0]
        with pytest.raises(KeyError):
            deployment.context.run_bindings.get(agent_run.agent_run_id)

        restarted = build_single_node_deployment(config)
        assert restarted.context.reconciliation.repaired == 1
        repaired = restarted.context.run_bindings.get(agent_run.agent_run_id)
        assert repaired.run_id == agent_run.run_id
        assert repaired.context_bundle_id == bundle.context_bundle_id
        assert repaired.context_bundle_digest == bundle.digest

        restarted_again = build_single_node_deployment(config)
        assert restarted_again.context.reconciliation.repaired == 0
        assert restarted_again.context.reconciliation.already_bound >= 1
        assert restarted_again.context.run_bindings.get(agent_run.agent_run_id) == repaired

    asyncio.run(scenario())
