from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeModelProvider


def _request(user_id: str, key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request:{key}",
        correlation_id=f"correlation:{key}",
        actor=ActorContext(
            principal_ref=user_id,
            owner_type="user",
            owner_id=user_id,
            actor_type="human",
        ),
    )


def test_public_context_inspection_reauthorizes_each_source_and_redacts_denied_entries(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "issue-650-redaction", secure_cookie=False)
        )
        provider = FakeModelProvider()
        deployment.models.register_provider(provider)
        model_id = "model-issue-650-redaction"
        deployment.models.register_model(
            ModelConfiguration(
                config_id=model_id,
                display_name="Issue 650 redaction model",
                provider_id=provider.descriptor.provider_id,
                capabilities=ModelCapabilities(context_window=65_536, modalities=("text",)),
                location=ModelLocation.LOCAL,
                health=HealthStatus.HEALTHY,
            )
        )
        admin = deployment.bootstrap_admin(
            "issue650-redaction-admin",
            "issue-650-redaction-password",
        )
        project = deployment.scopes.create_project(
            key="issue-650-redaction-project",
            name="Issue 650 redaction project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        agent = deployment.agents.create_agent(
            AgentProfile(
                name="Issue 650 redaction worker",
                role="worker",
                instructions=AgentInstructions(
                    role=InstructionSource(
                        content="Use only canonical authorized Context.",
                        version="issue-650-redaction-v1",
                    )
                ),
            ),
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project.id,
        )
        task = await deployment.kernel.create_task(
            idempotency_key="issue650-redaction-create",
            title="Source-level Context authorization",
            objective="Prove per-source Context inspection redaction.",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        await deployment.kernel.update_task(
            idempotency_key="issue650-redaction-bind-agent",
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
            idempotency_key="issue650-redaction-ready",
            task_id=task.task_id,
        )
        run = await deployment.kernel.start_task(
            idempotency_key="issue650-redaction-start",
            task_id=task.task_id,
            actor_ref=admin.user_id,
        )
        agent_runs = deployment.agents.repository.list_agent_runs(run.run_id)
        assert len(agent_runs) == 1
        binding = deployment.context.run_bindings.get(agent_runs[0].agent_run_id)

        viewer = deployment.authentication.create_local_user(
            "issue650-partial-viewer",
            "issue-650-viewer-password",
        )
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=viewer.user_id,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset(
                    {AuthorizationAction.READ, AuthorizationAction.VIEW}
                ),
                resource_types=frozenset({ResourceType.GENERIC, ResourceType.TASK}),
                project_ids=frozenset({project.id}),
            )
        )

        admin_view = await deployment.control_plane.get_extension_resource(
            _request(admin.user_id, "admin-read"),
            "context-bundles",
            binding.context_bundle_id,
        )
        viewer_view = await deployment.control_plane.get_extension_resource(
            _request(viewer.user_id, "viewer-read"),
            "context-bundles",
            binding.context_bundle_id,
        )
        admin_entries = {str(entry["source_type"]): entry for entry in admin_view["entries"]}
        viewer_entries = {str(entry["source_type"]): entry for entry in viewer_view["entries"]}

        assert admin_entries["task"]["hidden"] is False
        assert admin_entries["task"]["source_id"] == task.task_id
        assert admin_entries["agent"]["hidden"] is False
        assert admin_entries["agent"]["source_id"] == agent.agent_id

        assert viewer_entries["task"]["hidden"] is False
        assert viewer_entries["task"]["source_id"] == task.task_id
        assert viewer_entries["task"]["inline_content"] is None
        assert viewer_entries["agent"]["hidden"] is True
        for forbidden in ("source_id", "source_revision", "source_digest", "source_locator"):
            assert forbidden not in viewer_entries["agent"]
        assert "inline_content" not in viewer_entries["agent"]

    asyncio.run(scenario())
