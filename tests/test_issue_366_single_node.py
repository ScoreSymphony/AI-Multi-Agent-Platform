from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.backup.inventory import optional_single_node_store_paths
from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentAccessContext,
    CapabilityAssignmentContent,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
    JsonCapabilityAssignmentRepository,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.templates import (
    CapabilityAssignmentTemplateHandler,
    TemplateType,
)

PASSWORD = "correct horse battery staple"


def test_single_node_composes_durable_canonical_capability_assignments(tmp_path: Path) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    deployment = build_single_node_deployment(config)
    admin = deployment.bootstrap_admin("admin", PASSWORD)
    project = deployment.scopes.create_project(
        key="issue-366-project",
        name="Issue 366",
        owner_type="user",
        owner_id=admin.user_id,
    )
    access = CapabilityAssignmentAccessContext(
        actor=ActorIdentity(actor_id=admin.user_id, actor_type=ActorType.HUMAN),
        operation=OperationContext(
            correlation_id="issue-366-single-node",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        ),
    )

    created = asyncio.run(
        deployment.capability_assignments.create(
            owner_ref=project.owner_ref,
            content=CapabilityAssignmentContent(
                target=CapabilityAssignmentTarget(
                    subject_type=CapabilityAssignmentTargetType.PROJECT,
                    subject_id=project.id,
                )
            ),
            access=access,
            project_id=project.id,
        )
    )

    assert isinstance(
        deployment.capability_assignments.repository,
        JsonCapabilityAssignmentRepository,
    )
    handler = deployment.templates.handlers.get(TemplateType.CAPABILITY_ASSIGNMENT)
    assert isinstance(handler, CapabilityAssignmentTemplateHandler)
    assert "db/capability-assignments.json" in optional_single_node_store_paths()
    assert (config.database_dir / "capability-assignments.json").is_file()

    restarted = build_single_node_deployment(config)
    restored = restarted.capability_assignments.repository.get(created.assignment_id)
    assert restored.assignment_id == created.assignment_id
    assert restored.current_revision == 1
    assert restored.project_id == project.id
