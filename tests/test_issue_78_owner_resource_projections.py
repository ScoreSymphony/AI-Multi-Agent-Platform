from __future__ import annotations

import asyncio

from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentAccessContext,
    CapabilityAssignmentContent,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.workflows import WorkflowCallContext, WorkflowContent, WorkflowStage


def test_single_node_reads_template_generated_owner_domains_through_canonical_services(
    tmp_path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        owner = OwnerRef(type="user", id=admin.user_id)

        workflow = await deployment.workflows.create(
            context=WorkflowCallContext(
                operation=OperationContext(
                    correlation_id="issue-78-workflow-create",
                    owner_type="user",
                    owner_id=admin.user_id,
                ),
                actor_ref=admin.user_id,
            ),
            owner_ref=owner,
            content=WorkflowContent(
                name="Template-linked workflow",
                description="Canonical read projection regression.",
                stages=(WorkflowStage(stage_id="one", title="One"),),
            ),
        )

        project = deployment.scopes.create_project(
            key="issue-78-owner-projection",
            name="Issue 78 owner projection",
            owner_type="user",
            owner_id=admin.user_id,
        )
        assignment = await deployment.capability_assignments.create(
            owner_ref=owner,
            content=CapabilityAssignmentContent(
                target=CapabilityAssignmentTarget(
                    CapabilityAssignmentTargetType.PROJECT,
                    project.id,
                )
            ),
            access=CapabilityAssignmentAccessContext(
                actor=ActorIdentity(admin.user_id, ActorType.HUMAN),
                operation=OperationContext(
                    correlation_id="issue-78-assignment-create",
                    owner_type="user",
                    owner_id=admin.user_id,
                    project_id=project.id,
                ),
            ),
            project_id=project.id,
        )

        request = RequestContext(
            request_id="request-issue-78-owner-projection",
            correlation_id="issue-78-owner-projection",
            actor=ActorContext(
                principal_ref=admin.user_id,
                owner_type="user",
                owner_id=admin.user_id,
                actor_type=ActorType.HUMAN.value,
            ),
        )

        workflow_resource = await deployment.control_plane.get_extension_resource(
            request,
            "workflows",
            workflow.workflow_id,
        )
        assignment_resource = await deployment.control_plane.get_extension_resource(
            request,
            "capability-assignments",
            assignment.assignment_id,
        )

        assert workflow_resource["id"] == workflow.workflow_id
        assert workflow_resource["type"] == "workflow"
        assert workflow_resource["current_revision"] == workflow.revision
        assert assignment_resource["id"] == assignment.assignment_id
        assert assignment_resource["type"] == "capability_assignment"
        assert assignment_resource["current_revision"] == assignment.revision

    asyncio.run(scenario())
