"""Explicit Control Plane registration for portability workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.portability.workflow import (
    ExportSelection,
    PortabilityWorkflowService,
    import_preview_to_dict,
    import_report_to_dict,
    package_inspection_to_dict,
)

from .extensions import ControlPlaneModule, ResourceService
from .models import PageQuery, RequestContext

PORTABILITY_PACKAGE_COLLECTION = "portability-packages"
PORTABILITY_PREVIEW_COLLECTION = "portability-import-previews"
PORTABILITY_REPORT_COLLECTION = "portability-import-reports"
PORTABILITY_COLLECTIONS = (
    PORTABILITY_PACKAGE_COLLECTION,
    PORTABILITY_PREVIEW_COLLECTION,
    PORTABILITY_REPORT_COLLECTION,
)
PORTABILITY_COMMANDS = (
    "portability.export",
    "portability.package.validate",
    "portability.preview",
    "portability.import",
)
PORTABILITY_MODULE = "portability"


class _PackageResources(ResourceService):
    def __init__(self, workflow: PortabilityWorkflowService) -> None:
        self._workflow = workflow

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(package_inspection_to_dict(item) for item in self._workflow.list_packages())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return package_inspection_to_dict(self._workflow.package(resource_id))


class _PreviewResources(ResourceService):
    def __init__(self, workflow: PortabilityWorkflowService) -> None:
        self._workflow = workflow

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(import_preview_to_dict(item) for item in self._workflow.list_previews())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return import_preview_to_dict(self._workflow.preview(resource_id))


class _ReportResources(ResourceService):
    def __init__(self, workflow: PortabilityWorkflowService) -> None:
        self._workflow = workflow

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(import_report_to_dict(item) for item in self._workflow.list_reports())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return import_report_to_dict(self._workflow.report(resource_id))


class PortabilityCommandHandlers:
    """Command adapter around the portability domain service."""

    def __init__(self, workflow: PortabilityWorkflowService) -> None:
        self.workflow = workflow

    async def export(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del resource_ref
        _require_only(payload, {"resources", "metadata"})
        resources = payload.get("resources")
        if not isinstance(resources, list) or not resources:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "portability export requires a non-empty resources array",
                details={"field": "resources"},
            )
        selections: list[ExportSelection] = []
        for index, item in enumerate(resources):
            if not isinstance(item, dict):
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "portability export resource must be an object",
                    details={"field": f"resources[{index}]"},
                )
            _require_only(item, {"resource_type", "resource_id"})
            selections.append(
                ExportSelection(
                    resource_type=_required_string(item, "resource_type"),
                    resource_id=_required_string(item, "resource_id"),
                )
            )
        metadata = payload.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "portability export metadata must be an object",
                details={"field": "metadata"},
            )
        inspection = await self.workflow.export_package(
            selections,
            author=context.actor.principal_ref,
            metadata=metadata,
        )
        return package_inspection_to_dict(inspection)

    async def validate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context, resource_ref
        _require_only(payload, {"package"})
        if "package" not in payload:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "portability package validation requires package",
                details={"field": "package"},
            )
        inspection = self.workflow.validate_package_document(payload["package"])
        return package_inspection_to_dict(inspection)

    async def preview(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, set())
        return import_preview_to_dict(self.workflow.preview_import(resource_ref))

    async def import_package(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, set())
        return import_report_to_dict(await self.workflow.execute_import(resource_ref))


def portability_control_plane_module(
    workflow: PortabilityWorkflowService,
) -> ControlPlaneModule:
    """Build the complete, explicitly owned portability northbound contribution."""

    handlers = PortabilityCommandHandlers(workflow)
    return ControlPlaneModule(
        name=PORTABILITY_MODULE,
        resource_services={
            PORTABILITY_PACKAGE_COLLECTION: _PackageResources(workflow),
            PORTABILITY_PREVIEW_COLLECTION: _PreviewResources(workflow),
            PORTABILITY_REPORT_COLLECTION: _ReportResources(workflow),
        },
        command_handlers={
            "portability.export": handlers.export,
            "portability.package.validate": handlers.validate,
            "portability.preview": handlers.preview,
            "portability.import": handlers.import_package,
        },
    )


def _required_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be a non-blank string",
            details={"field": field},
        )
    return value


def _require_only(payload: Mapping[str, object], allowed: set[str]) -> None:
    unexpected = sorted(set(payload).difference(allowed))
    if unexpected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "unexpected portability command fields",
            details={"fields": cast(JsonValue, unexpected)},
        )


__all__ = [
    "PORTABILITY_COLLECTIONS",
    "PORTABILITY_COMMANDS",
    "PORTABILITY_MODULE",
    "PORTABILITY_PACKAGE_COLLECTION",
    "PORTABILITY_PREVIEW_COLLECTION",
    "PORTABILITY_REPORT_COLLECTION",
    "PortabilityCommandHandlers",
    "portability_control_plane_module",
]
