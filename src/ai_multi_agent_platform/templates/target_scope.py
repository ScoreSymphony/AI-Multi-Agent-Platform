"""Authorization preflight for canonical target scopes referenced by Templates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.workspaces import WorkspaceProvider

from .access import TemplateScopeAccess
from .application import TemplateApplicationService
from .models import TemplateRevision, TemplateType

_TARGET_SCOPE_TEMPLATE_TYPES = frozenset(
    {
        TemplateType.AGENT,
        TemplateType.AGENT_TEAM,
        TemplateType.AUTOMATION,
    }
)


async def authorize_template_target_scopes(
    application: TemplateApplicationService,
    scope_access: TemplateScopeAccess | None,
    context: RequestContext,
    action: str,
    template_id: str,
    *,
    revision: int | None,
    allow_draft: bool,
    request_payload_digest: str | None = None,
) -> None:
    """Authorize every canonical Project/Workspace a Template graph can target.

    Template definition authorization answers whether an actor may use a Template. It does
    not imply that the actor may create the resulting canonical resources inside arbitrary
    Project or Workspace scopes encoded by manually-authored configuration. Resolve those
    targets from the exact graph before any handler is allowed to create resources.
    """

    if scope_access is None:
        return

    root = application._get_revision(
        template_id,
        revision,
        published_only=not allow_draft,
    )
    dependency_order, _ = application._resolve_dependency_order(root)

    project_ids: set[str] = set()
    workspace_ids: set[str] = set()
    for item in dependency_order:
        projects, workspaces = _target_scope_refs(item)
        project_ids.update(projects)
        workspace_ids.update(workspaces)

    for project_id in sorted(project_ids):
        project = scope_access.control_plane.scopes.get_project(project_id)
        await scope_access.authorize(
            context,
            action,
            project.id,
            owner_ref=project.owner_ref,
            project_id=project.id,
            request_payload_digest=request_payload_digest,
        )

    if not workspace_ids:
        return

    workspace_provider = cast(
        WorkspaceProvider | None,
        getattr(scope_access.control_plane, "workspace_provider", None),
    )
    if workspace_provider is None:
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            "Template references Workspace targets but no canonical WorkspaceProvider is composed",
            details={"workspace_ids": cast(JsonValue, sorted(workspace_ids))},
        )

    for workspace_id in sorted(workspace_ids):
        workspace = await workspace_provider.get_workspace(workspace_id)
        await scope_access.authorize(
            context,
            action,
            workspace.id,
            owner_ref=workspace.owner_ref,
            project_id=workspace.project_id,
            request_payload_digest=request_payload_digest,
        )


def _target_scope_refs(revision: TemplateRevision) -> tuple[set[str], set[str]]:
    if revision.content.template_type not in _TARGET_SCOPE_TEMPLATE_TYPES:
        return set(), set()

    payload = revision.content.configuration.payload
    if payload is None:
        # External configuration references are validated/materialized by their own
        # server-owned binding path. They must not be guessed from caller claims here.
        return set(), set()
    if not isinstance(payload, Mapping):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Template configuration payload must be an object before target-scope validation",
            details={"template_id": revision.template_id, "revision": revision.revision},
        )

    project_ids: set[str] = set()
    workspace_ids: set[str] = set()
    _collect_pair(payload, project_ids, workspace_ids, path="configuration")

    if revision.content.template_type is TemplateType.AGENT:
        profile = _optional_mapping(payload, "profile", path="configuration")
        if profile is not None:
            defaults = _optional_mapping(
                profile, "workspace_defaults", path="configuration.profile"
            )
            if defaults is not None:
                _collect_pair(
                    defaults,
                    project_ids,
                    workspace_ids,
                    path="configuration.profile.workspace_defaults",
                )

    if revision.content.template_type is TemplateType.AUTOMATION:
        task_template = _optional_mapping(payload, "task_template", path="configuration")
        if task_template is not None:
            _collect_pair(
                task_template,
                project_ids,
                workspace_ids,
                path="configuration.task_template",
            )

    return project_ids, workspace_ids


def _collect_pair(
    value: Mapping[str, object],
    project_ids: set[str],
    workspace_ids: set[str],
    *,
    path: str,
) -> None:
    project_id = _optional_scope_id(value, "project_id", path=path)
    workspace_id = _optional_scope_id(value, "workspace_id", path=path)
    if project_id is not None:
        project_ids.add(project_id)
    if workspace_id is not None:
        workspace_ids.add(workspace_id)


def _optional_mapping(
    value: Mapping[str, object],
    key: str,
    *,
    path: str,
) -> Mapping[str, object] | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, Mapping):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{path}.{key} must be an object",
        )
    return cast(Mapping[str, object], item)


def _optional_scope_id(
    value: Mapping[str, object],
    key: str,
    *,
    path: str,
) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{path}.{key} must be a non-blank canonical ID when provided",
        )
    return item
