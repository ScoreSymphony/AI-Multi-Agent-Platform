"""Rollback-capable canonical Project import for issue #79."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.domain import Project

from .models import PortableResource
from .project_codecs import PROJECT_RESOURCE_TYPE
from .registry import ImportContext

ProjectDependencyAudit = Callable[[str], tuple[str, ...] | None]


class ProjectImportMutationHandler:
    """Write complete Projects through ScopeStore and compensate only with safety proof."""

    resource_type = PROJECT_RESOURCE_TYPE

    def __init__(
        self,
        scopes: ScopeStore,
        *,
        dependency_audit: ProjectDependencyAudit | None = None,
    ) -> None:
        self._scopes = scopes
        self._dependency_audit = dependency_audit

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        project = _require_project(value)
        try:
            self._scopes.get_project(project.id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        raise ContractError(
            ErrorCode.CONFLICT,
            f"Project appeared after import preview: {project.id}",
            details={"project_id": project.id},
        )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del context
        project = _require_project(value)
        key = f"portability-project:{resource.checksum}:{project.id}"
        self._scopes.store_project_snapshot(key=key, project=project)
        return project.id

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Project rollback token must be the imported Project ID",
            )
        dependencies = (
            None if self._dependency_audit is None else self._dependency_audit(token)
        )
        self._scopes.compensate_project(
            token,
            external_dependencies=dependencies,
        )


def _require_project(value: object) -> Project:
    if not isinstance(value, Project):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable Project mutation handler received the wrong decoded resource type",
        )
    return value


__all__ = ["ProjectDependencyAudit", "ProjectImportMutationHandler"]
