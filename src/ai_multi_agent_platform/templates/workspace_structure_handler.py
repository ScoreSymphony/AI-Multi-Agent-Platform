"""Concrete Template integration for canonical #37 Workspace structures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    FrozenJsonValue,
    OperationContext,
    OperationControl,
)
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import OwnerRef, Project
from ai_multi_agent_platform.workspaces import (
    Workspace,
    WorkspaceAccessMode,
    WorkspaceProvider,
    WorkspaceRetention,
    WorkspaceType,
)

from .application import ContextualTemplateHandlerRegistry, TemplateInstantiationContext
from .models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from .service import TemplateService

_SUPPORTED_STRUCTURE_TYPES = frozenset({WorkspaceType.PERSISTENT_PROJECT})


@dataclass(frozen=True, slots=True)
class _WorkspaceSpec:
    workspace_type: WorkspaceType
    access_mode: WorkspaceAccessMode
    retention: WorkspaceRetention


@dataclass(slots=True)
class WorkspaceStructureTemplateHandler:
    """Create canonical empty Workspaces through the #37 WorkspaceProvider."""

    provider: WorkspaceProvider
    scopes: ScopeStore
    template_type = TemplateType.WORKSPACE_STRUCTURE

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        payload = _payload(revision)
        _project_selector(revision, payload, self.scopes)
        specs = _workspace_specs(payload)
        return tuple(
            TemplateResourceChange(
                resource_type="workspace",
                action="create",
                description=(
                    f"Create {spec.workspace_type.value} Workspace "
                    f"from {revision.template_id}@{revision.revision}"
                ),
            )
            for spec in specs
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        payload = _payload(revision)
        project = _resolve_project(revision, payload, context, self.scopes)
        specs = _workspace_specs(payload)
        resources: list[TemplateResourceRef] = []
        for index, spec in enumerate(specs):
            key = (
                f"template:{context.instance_id}:{revision.template_id}:"
                f"{revision.revision}:workspace:{index}"
            )
            data_context = DataAccessContext(
                operation=OperationContext(
                    correlation_id=f"template:{context.instance_id}",
                    owner_type=project.owner_ref.type,
                    owner_id=project.owner_ref.id,
                    project_id=project.id,
                    control=OperationControl(idempotency_key=key),
                ),
                actor_ref=provenance.applied_by.id,
            )
            workspace = await self.provider.create_workspace(
                project_id=project.id,
                owner_ref=project.owner_ref,
                workspace_type=spec.workspace_type,
                context=data_context,
                access_mode=spec.access_mode,
                retention=spec.retention,
            )
            resources.append(
                TemplateResourceRef(resource_type="workspace", resource_id=workspace.id)
            )
        return tuple(resources)


@dataclass(slots=True)
class WorkspaceStructureTemplateExporter:
    """Export empty Workspace topology without snapshots, files, runtime state or source data."""

    provider: WorkspaceProvider
    templates: TemplateService

    async def create_from_workspaces(
        self,
        workspace_ids: Sequence[str],
        *,
        owner_ref: OwnerRef,
        author: str,
        name: str,
        project_template_id: str | None = None,
        project_template_revision: int | None = None,
    ) -> TemplateRevision:
        if not workspace_ids:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Workspace structure export requires at least one Workspace",
            )
        workspaces = tuple(
            [await self.provider.get_workspace(workspace_id) for workspace_id in workspace_ids]
        )
        project_ids = {workspace.project_id for workspace in workspaces}
        if len(project_ids) != 1:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Workspace structure export requires Workspaces from one Project",
            )
        for workspace in workspaces:
            await _require_empty_workspace(self.provider, workspace)
            if workspace.workspace_type not in _SUPPORTED_STRUCTURE_TYPES:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Workspace structure Templates currently support persistent Project Workspaces",
                    details={"workspace_type": workspace.workspace_type.value},
                )
            if workspace.retention is not WorkspaceRetention.PERSISTENT:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Workspace structure Templates cannot export runtime retention state",
                    details={"retention": workspace.retention.value},
                )

        source_project_id = next(iter(project_ids))
        workspace_specs: tuple[FrozenJsonValue, ...] = tuple(
            {
                "workspace_type": workspace.workspace_type.value,
                "access_mode": workspace.access_mode.value,
                "retention": workspace.retention.value,
            }
            for workspace in workspaces
        )
        configuration: dict[str, FrozenJsonValue] = {"workspaces": workspace_specs}
        dependencies: tuple[TemplateDependency, ...] = ()
        if project_template_id is None:
            configuration["project_id"] = source_project_id
        else:
            configuration["project_template_id"] = project_template_id
            if project_template_revision is not None:
                configuration["project_template_revision"] = project_template_revision
            dependencies = (
                TemplateDependency(
                    template_id=project_template_id,
                    revision=project_template_revision,
                ),
            )

        content = TemplateContent(
            name=name,
            description="Reusable canonical Workspace structure",
            template_type=TemplateType.WORKSPACE_STRUCTURE,
            configuration=TemplateConfiguration(payload=configuration),
            dependencies=dependencies,
            requirements=TemplateRequirements(),
            provenance=TemplateProvenance(
                author=author,
                source="canonical-workspace-structure-export",
                trust=TemplateTrust.LOCAL,
                metadata={
                    "source_resource_type": "workspace_structure",
                    "source_project_id": source_project_id,
                    "source_workspace_ids": tuple(workspace_ids),
                },
            ),
            tags=("workspace", "structure", "exported"),
        )
        return self.templates.create_draft(owner_ref=owner_ref, content=content)


def register_workspace_structure_template_handler(
    registry: ContextualTemplateHandlerRegistry,
    provider: WorkspaceProvider,
    scopes: ScopeStore,
) -> None:
    registry.register(WorkspaceStructureTemplateHandler(provider, scopes))


async def _require_empty_workspace(provider: WorkspaceProvider, workspace: Workspace) -> None:
    if workspace.source_refs:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Workspace structure export does not copy source references; use explicit composition",
            details={"workspace_id": workspace.id},
        )
    if workspace.base_snapshot_id is None:
        return
    snapshot = await provider.get_snapshot(workspace.base_snapshot_id)
    if snapshot.files:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Workspace structure export does not copy Workspace file content",
            details={"workspace_id": workspace.id},
        )


def _payload(revision: TemplateRevision) -> Mapping[str, object]:
    payload = revision.content.configuration.payload
    if payload is None:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Workspace structure handler requires an inline Template payload",
        )
    return cast(Mapping[str, object], payload)


def _project_selector(
    revision: TemplateRevision,
    payload: Mapping[str, object],
    scopes: ScopeStore,
) -> tuple[str, int | None, str | None]:
    template_id = _optional_string(payload, "project_template_id")
    template_revision = _optional_positive_int(payload, "project_template_revision")
    project_id = _optional_string(payload, "project_id")
    if (template_id is None) == (project_id is None):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            (
                "Workspace structure Template requires exactly one of "
                "project_template_id or project_id"
            ),
        )
    if template_id is not None:
        declared = {dependency.template_id for dependency in revision.content.dependencies}
        if template_id not in declared:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Workspace structure Project Template must be a declared dependency",
                details={"template_id": template_id},
            )
    else:
        assert project_id is not None
        scopes.get_project(project_id)
    return template_id or "", template_revision, project_id


def _resolve_project(
    revision: TemplateRevision,
    payload: Mapping[str, object],
    context: TemplateInstantiationContext,
    scopes: ScopeStore,
) -> Project:
    template_id, template_revision, project_id = _project_selector(revision, payload, scopes)
    if project_id is not None:
        return scopes.get_project(project_id)
    resource = context.single_resource_for(
        template_id,
        revision=template_revision,
        resource_type="project",
    )
    return scopes.get_project(resource.resource_id)


def _workspace_specs(payload: Mapping[str, object]) -> tuple[_WorkspaceSpec, ...]:
    raw_specs = payload.get("workspaces")
    if not isinstance(raw_specs, list | tuple) or not raw_specs:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Workspace structure Template requires a non-empty workspaces array",
        )
    if len(raw_specs) > 100:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Workspace structure Template is limited to 100 Workspaces",
        )
    return tuple(_workspace_spec(raw, index) for index, raw in enumerate(raw_specs))


def _workspace_spec(value: object, index: int) -> _WorkspaceSpec:
    if not isinstance(value, Mapping):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"workspaces[{index}] must be an object",
        )
    data = cast(Mapping[str, object], value)
    try:
        workspace_type = WorkspaceType(
            _optional_string(data, "workspace_type") or WorkspaceType.PERSISTENT_PROJECT.value
        )
        access_mode = WorkspaceAccessMode(
            _optional_string(data, "access_mode") or WorkspaceAccessMode.READ_WRITE.value
        )
        retention = WorkspaceRetention(
            _optional_string(data, "retention") or WorkspaceRetention.PERSISTENT.value
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"workspaces[{index}] contains an unsupported enum value",
        ) from exc
    if workspace_type not in _SUPPORTED_STRUCTURE_TYPES:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "Workspace structure Templates currently support persistent Project Workspaces",
            details={"workspace_type": workspace_type.value},
        )
    if retention is not WorkspaceRetention.PERSISTENT:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "Workspace structure Templates cannot create runtime retention state",
            details={"retention": retention.value},
        )
    return _WorkspaceSpec(
        workspace_type=workspace_type,
        access_mode=access_mode,
        retention=retention,
    )


def _optional_string(payload: Mapping[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{name} must be a non-blank string or null",
        )
    return value


def _optional_positive_int(payload: Mapping[str, object], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{name} must be an integer >= 1 or null",
        )
    return value
