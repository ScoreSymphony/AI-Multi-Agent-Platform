from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates import (
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    TemplateApplicationService,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateEnvironment,
    TemplateProvenance,
    TemplateType,
    register_project_template_handler,
)
from ai_multi_agent_platform.templates.workspace_structure_handler import (
    WorkspaceStructureTemplateExporter,
    register_workspace_structure_template_handler,
)
from ai_multi_agent_platform.workspaces import LocalWorkspaceProvider, WorkspaceType


def test_workspace_structure_resolves_new_project_dependency(tmp_path: Path) -> None:
    async def scenario() -> None:
        scopes = ScopeStore()
        files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        handlers = ContextualTemplateHandlerRegistry()
        register_project_template_handler(handlers, scopes)
        register_workspace_structure_template_handler(handlers, workspaces, scopes)
        application = TemplateApplicationService(InMemoryTemplateRepository(), handlers)

        project_draft = application.templates.create_draft(
            owner_ref=OwnerRef(type="user", id="template-owner"),
            content=TemplateContent(
                name="Research Project",
                description="Reusable Project dependency",
                template_type=TemplateType.PROJECT,
                configuration=TemplateConfiguration(payload={"name": "Research Project"}),
                provenance=TemplateProvenance(author="user:author", source="test"),
            ),
        )
        project_published = application.templates.publish(
            project_draft.template_id,
            expected_revision=project_draft.revision,
        )
        workspace_draft = application.templates.create_draft(
            owner_ref=OwnerRef(type="user", id="template-owner"),
            content=TemplateContent(
                name="Research Workspace Structure",
                description="One persistent Project Workspace",
                template_type=TemplateType.WORKSPACE_STRUCTURE,
                configuration=TemplateConfiguration(
                    payload={
                        "project_template_id": project_draft.template_id,
                        "project_template_revision": project_published.revision,
                        "workspaces": (
                            {
                                "workspace_type": "persistent_project",
                                "access_mode": "read_write",
                                "retention": "persistent",
                            },
                        ),
                    }
                ),
                dependencies=(
                    TemplateDependency(
                        template_id=project_draft.template_id,
                        revision=project_published.revision,
                    ),
                ),
                provenance=TemplateProvenance(author="user:author", source="test"),
            ),
        )
        workspace_published = application.templates.publish(
            workspace_draft.template_id,
            expected_revision=workspace_draft.revision,
        )

        preview = application.preview(
            workspace_draft.template_id,
            applied_by=OwnerRef(type="user", id="destination-owner"),
            environment=TemplateEnvironment(),
        )
        assert preview.applicable is True
        assert [change.resource_type for change in preview.resource_changes] == [
            "project",
            "workspace",
        ]

        instance = await application.apply(
            workspace_draft.template_id,
            applied_by=OwnerRef(type="user", id="destination-owner"),
            environment=TemplateEnvironment(),
            revision=workspace_published.revision,
        )
        assert [item.resource_type for item in instance.resource_refs] == [
            "project",
            "workspace",
        ]
        project_ref, workspace_ref = instance.resource_refs
        project = scopes.get_project(project_ref.resource_id)
        workspace = await workspaces.get_workspace(workspace_ref.resource_id)
        assert workspace.project_id == project.id
        assert workspace.owner_ref == project.owner_ref
        assert project.owner_ref == OwnerRef(type="user", id="destination-owner")
        assert workspace.base_snapshot_id is not None
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)
        assert snapshot.files == ()

    asyncio.run(scenario())


def test_workspace_structure_export_keeps_source_ids_out_of_configuration(tmp_path: Path) -> None:
    async def scenario() -> None:
        scopes = ScopeStore()
        source_project = scopes.create_project(
            key="source-project",
            name="Source Project",
            owner_type="user",
            owner_id="source-owner",
        )
        files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        handlers = ContextualTemplateHandlerRegistry()
        register_project_template_handler(handlers, scopes)
        register_workspace_structure_template_handler(handlers, workspaces, scopes)
        application = TemplateApplicationService(InMemoryTemplateRepository(), handlers)

        source_workspace = await workspaces.create_workspace(
            project_id=source_project.id,
            owner_ref=source_project.owner_ref,
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=DataAccessContext(
                operation=OperationContext(
                    correlation_id="workspace-template-export",
                    owner_type=source_project.owner_ref.type,
                    owner_id=source_project.owner_ref.id,
                    project_id=source_project.id,
                ),
                actor_ref="source-owner",
            ),
        )
        project_template = application.templates.create_draft(
            owner_ref=OwnerRef(type="user", id="template-owner"),
            content=TemplateContent(
                name="Destination Project",
                description="Project dependency",
                template_type=TemplateType.PROJECT,
                configuration=TemplateConfiguration(payload={"name": "Destination Project"}),
                provenance=TemplateProvenance(author="user:author", source="test"),
            ),
        )
        project_published = application.templates.publish(
            project_template.template_id,
            expected_revision=project_template.revision,
        )
        exporter = WorkspaceStructureTemplateExporter(workspaces, application.templates)
        exported = await exporter.create_from_workspaces(
            (source_workspace.id,),
            owner_ref=OwnerRef(type="user", id="template-owner"),
            author="user:author",
            name="Portable Workspace Structure",
            project_template_id=project_template.template_id,
            project_template_revision=project_published.revision,
        )

        payload = exported.content.configuration.payload
        assert payload is not None
        assert payload["project_template_id"] == project_template.template_id
        assert source_workspace.id not in repr(payload)
        assert source_project.id not in repr(payload)
        assert exported.content.provenance.metadata["source_project_id"] == source_project.id
        assert source_workspace.id in exported.content.provenance.metadata["source_workspace_ids"]

    asyncio.run(scenario())
