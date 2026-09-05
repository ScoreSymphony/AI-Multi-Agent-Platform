from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.automation import (
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef

PASSWORD = "correct horse battery staple"


def _profile() -> AgentProfile:
    return AgentProfile(
        name="Single-node template source",
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Work through the assigned task."),
        ),
    )


def test_single_node_wires_durable_agent_templates_and_control_plane(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        source = deployment.agents.create_agent(_profile(), owner_ref=owner)
        context = RequestContext(
            request_id="request-template-single-node",
            correlation_id="correlation-template-single-node",
            actor=ActorContext(
                principal_ref=admin.user_id,
                owner_type="user",
                owner_id=admin.user_id,
            ),
            idempotency_key="template-single-node-create",
        )

        assert "templates" in deployment.control_plane.registered_collections
        assert "template-instances" in deployment.control_plane.registered_collections
        assert "template.create-from-agent" in deployment.control_plane.registered_commands
        assert "template.create-from-automation" in deployment.control_plane.registered_commands
        assert "template.create-from-project" in deployment.control_plane.registered_commands
        assert "template.create-from-workspaces" in deployment.control_plane.registered_commands
        assert "template.apply" in deployment.control_plane.registered_commands

        created = await deployment.control_plane.execute_command(
            context,
            "template.create-from-agent",
            "templates",
            {"agent_id": source.agent_id},
        )
        template_id = created["id"]
        assert isinstance(template_id, str)

        published = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-template-publish",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="template-single-node-publish",
            ),
            "template.publish",
            template_id,
            {"expected_revision": 1},
        )
        assert published["latest_published_revision"] == 2

        read_context = RequestContext(
            request_id="request-template-read",
            correlation_id=context.correlation_id,
            actor=context.actor,
        )
        listed = await deployment.control_plane.list_extension_resources(
            read_context,
            "templates",
            PageQuery(),
        )
        assert [item["id"] for item in listed["items"]] == [template_id]
        detail = await deployment.control_plane.get_extension_resource(
            read_context,
            "templates",
            template_id,
        )
        assert detail["id"] == template_id

        applied = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-template-apply",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="template-single-node-apply",
            ),
            "template.apply",
            template_id,
            {},
        )
        instance_id = applied["id"]
        assert isinstance(instance_id, str)
        instance = deployment.templates.repository.get_instantiation(instance_id)
        assert len(instance.resource_refs) == 1
        created_agent_id = instance.resource_refs[0].resource_id
        assert created_agent_id != source.agent_id
        assert deployment.agents.get_agent_revision(created_agent_id).profile == source.profile

        instance_list = await deployment.control_plane.list_extension_resources(
            read_context,
            "template-instances",
            PageQuery(),
        )
        assert [item["id"] for item in instance_list["items"]] == [instance_id]
        instance_detail = await deployment.control_plane.get_extension_resource(
            read_context,
            "template-instances",
            instance_id,
        )
        assert instance_detail["id"] == instance_id

        source_automation = await deployment.control_plane.automation_service.create_automation(
            name="Single-node Automation template source",
            description="Reusable manual Automation",
            identity=IdentityContext(
                principal_ref=admin.user_id,
                owner_type="user",
                owner_id=admin.user_id,
            ),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=TaskTemplate(
                title="Template-generated task",
                objective="Verify Automation Template instantiation",
            ),
        )
        automation_template = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-automation-template-create",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="automation-template-create",
            ),
            "template.create-from-automation",
            "templates",
            {"automation_id": source_automation.id},
        )
        automation_template_id = automation_template["id"]
        assert isinstance(automation_template_id, str)
        await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-automation-template-publish",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="automation-template-publish",
            ),
            "template.publish",
            automation_template_id,
            {"expected_revision": 1},
        )
        automation_applied = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-automation-template-apply",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="automation-template-apply",
            ),
            "template.apply",
            automation_template_id,
            {},
        )
        automation_instance_id = automation_applied["id"]
        assert isinstance(automation_instance_id, str)
        automation_instance = deployment.templates.repository.get_instantiation(
            automation_instance_id
        )
        assert len(automation_instance.resource_refs) == 1
        automation_ref = automation_instance.resource_refs[0]
        assert automation_ref.resource_type == "automation"
        assert automation_ref.resource_id != source_automation.id
        created_automation = await deployment.control_plane.automation_service.get_automation(
            automation_ref.resource_id
        )
        assert created_automation.name == source_automation.name
        assert created_automation.description == source_automation.description
        assert created_automation.trigger == source_automation.trigger
        assert created_automation.task_template == source_automation.task_template
        assert created_automation.identity.owner_id == admin.user_id

        source_project = deployment.scopes.create_project(
            key="template-source-project",
            name="Template source project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        source_workspace_resource = await deployment.control_plane.create_workspace(
            RequestContext(
                request_id="request-template-source-workspace",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="template-source-workspace",
            ),
            {"project_id": source_project.id},
        )
        source_workspace_id = source_workspace_resource["id"]
        assert isinstance(source_workspace_id, str)

        project_template = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-project-template-create",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="project-template-create",
            ),
            "template.create-from-project",
            "templates",
            {"project_id": source_project.id},
        )
        project_template_id = project_template["id"]
        assert isinstance(project_template_id, str)
        project_template_published = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-project-template-publish",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="project-template-publish",
            ),
            "template.publish",
            project_template_id,
            {"expected_revision": 1},
        )
        project_template_revision = project_template_published["latest_published_revision"]
        assert project_template_revision == 2

        workspace_template = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-workspace-template-create",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="workspace-template-create",
            ),
            "template.create-from-workspaces",
            "templates",
            {
                "workspace_ids": [source_workspace_id],
                "name": "Portable Workspace Structure",
                "project_template_id": project_template_id,
                "project_template_revision": project_template_revision,
            },
        )
        workspace_template_id = workspace_template["id"]
        assert isinstance(workspace_template_id, str)
        workspace_template_published = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-workspace-template-publish",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="workspace-template-publish",
            ),
            "template.publish",
            workspace_template_id,
            {"expected_revision": 1},
        )
        assert workspace_template_published["latest_published_revision"] == 2

        workspace_applied = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-workspace-template-apply",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="workspace-template-apply",
            ),
            "template.apply",
            workspace_template_id,
            {},
        )
        workspace_instance_id = workspace_applied["id"]
        assert isinstance(workspace_instance_id, str)
        workspace_instance = deployment.templates.repository.get_instantiation(
            workspace_instance_id
        )
        assert [item.resource_type for item in workspace_instance.resource_refs] == [
            "project",
            "workspace",
        ]
        generated_project_ref, generated_workspace_ref = workspace_instance.resource_refs
        assert generated_project_ref.resource_id != source_project.id
        assert generated_workspace_ref.resource_id != source_workspace_id
        generated_workspace = await deployment.workspaces.get_workspace(
            generated_workspace_ref.resource_id
        )
        assert generated_workspace.project_id == generated_project_ref.resource_id
        generated_project = deployment.scopes.get_project(generated_project_ref.resource_id)
        assert generated_project.owner_ref == owner
        assert generated_workspace.owner_ref == owner

        assert (config.database_dir / "templates.json").exists()

        restarted = build_single_node_deployment(config)
        restored = restarted.templates.repository.get_template(template_id)
        assert restored.latest_published_revision == 2
        assert restarted.templates.repository.get_instantiation(instance_id) == instance
        assert restarted.agents.get_agent_revision(created_agent_id).profile == source.profile
        restored_automation = await restarted.control_plane.automation_service.get_automation(
            automation_ref.resource_id
        )
        assert restored_automation.name == source_automation.name
        restored_workspace = await restarted.workspaces.get_workspace(
            generated_workspace_ref.resource_id
        )
        assert restored_workspace.project_id == generated_project_ref.resource_id
        assert restarted.scopes.get_project(generated_project_ref.resource_id).owner_ref == owner
        assert "template.create-from-agent" in restarted.control_plane.registered_commands
        assert "template.create-from-automation" in restarted.control_plane.registered_commands
        assert "template.create-from-project" in restarted.control_plane.registered_commands
        assert "template.create-from-workspaces" in restarted.control_plane.registered_commands

    asyncio.run(scenario())
