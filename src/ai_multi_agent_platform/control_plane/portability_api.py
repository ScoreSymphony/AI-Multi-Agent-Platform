"""Canonical Control Plane surface for issue #79 portability workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.portability.workflow import (
    ExportSelection,
    PortabilityWorkflowService,
    import_preview_to_dict,
    import_report_to_dict,
    package_inspection_to_dict,
)

from .conversation_current_composition import ControlPlane as _CurrentControlPlane
from .extensions import CommandHandler, ResourceService
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


class ControlPlane(_CurrentControlPlane):
    """Newest public composition plus optional canonical portability workflow."""

    def __init__(
        self,
        *args: Any,
        portability_workflow: PortabilityWorkflowService | None = None,
        **kwargs: Any,
    ) -> None:
        self._installing_portability = False
        if portability_workflow is not None:
            supplied_resources = kwargs.get("resource_services")
            if isinstance(supplied_resources, Mapping):
                conflicts = sorted(set(supplied_resources).intersection(PORTABILITY_COLLECTIONS))
                if conflicts:
                    raise ValueError(
                        "resource_services conflict with canonical portability routes: "
                        f"{conflicts!r}"
                    )
            supplied_commands = kwargs.get("command_handlers")
            if isinstance(supplied_commands, Mapping):
                conflicts = sorted(set(supplied_commands).intersection(PORTABILITY_COMMANDS))
                if conflicts:
                    raise ValueError(
                        "command_handlers conflict with canonical portability commands: "
                        f"{conflicts!r}"
                    )
        super().__init__(*args, **kwargs)
        self._portability_workflow = portability_workflow
        if portability_workflow is None:
            return

        self._installing_portability = True
        try:
            super().register_resource_service(
                PORTABILITY_PACKAGE_COLLECTION,
                _PackageResources(portability_workflow),
            )
            super().register_resource_service(
                PORTABILITY_PREVIEW_COLLECTION,
                _PreviewResources(portability_workflow),
            )
            super().register_resource_service(
                PORTABILITY_REPORT_COLLECTION,
                _ReportResources(portability_workflow),
            )
            super().register_command("portability.export", self._portability_export)
            super().register_command(
                "portability.package.validate",
                self._portability_validate,
            )
            super().register_command("portability.preview", self._portability_preview)
            super().register_command("portability.import", self._portability_import)
        finally:
            self._installing_portability = False

    @property
    def portability_workflow(self) -> PortabilityWorkflowService | None:
        return self._portability_workflow

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if collection in PORTABILITY_COLLECTIONS and not self._installing_portability:
            raise ValueError(
                f"extension collection conflicts with canonical portability route: {collection}"
            )
        super().register_resource_service(collection, service)

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if command in PORTABILITY_COMMANDS and not self._installing_portability:
            raise ValueError(
                f"extension command conflicts with canonical portability command: {command}"
            )
        super().register_command(command, handler)

    def _require_portability(self) -> PortabilityWorkflowService:
        if self._portability_workflow is None:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "portable import/export workflow is not configured",
            )
        return self._portability_workflow

    async def _portability_export(
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
        inspection = await self._require_portability().export_package(
            selections,
            author=context.actor.principal_ref,
            metadata=metadata,
        )
        return package_inspection_to_dict(inspection)

    async def _portability_validate(
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
        inspection = self._require_portability().validate_package_document(payload["package"])
        return package_inspection_to_dict(inspection)

    async def _portability_preview(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, set())
        stored = self._require_portability().preview_import(resource_ref)
        return import_preview_to_dict(stored)

    async def _portability_import(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, set())
        report = await self._require_portability().execute_import(resource_ref)
        return import_report_to_dict(report)


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
    "PORTABILITY_PACKAGE_COLLECTION",
    "PORTABILITY_PREVIEW_COLLECTION",
    "PORTABILITY_REPORT_COLLECTION",
    "ControlPlane",
]
