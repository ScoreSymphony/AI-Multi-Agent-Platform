"""Northbound-safe import/export workflow for issue #79.

This module composes the existing package, preview and executor primitives without
making the Control Plane reconstruct trusted import plans from client supplied data.
A validated package is registered server-side, previewed against destination state,
and only the exact stored preview can later authorize mutation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .executor import ImportExecutionResult, ImportExecutor
from .models import (
    CompatibilityMetadata,
    ExcludedState,
    PackageProvenance,
    PortablePackage,
)
from .package import build_package, package_from_dict, package_to_dict
from .planner import ImportPreview, ImportPreviewService
from .registry import ResourceSerializerRegistry

ExportLoader = Callable[[str], Awaitable[object]]
ExportExclusions = Callable[[object], tuple[ExcludedState, ...]]

_RELEASE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


@dataclass(frozen=True, slots=True)
class ExportSelection:
    resource_type: str
    resource_id: str

    def __post_init__(self) -> None:
        if not self.resource_type.strip():
            raise ValueError("export resource_type must not be blank")
        if not self.resource_id.strip():
            raise ValueError("export resource_id must not be blank")


@dataclass(frozen=True, slots=True)
class _ExportSource:
    loader: ExportLoader
    exclusions: ExportExclusions | None = None


class ExportSourceRegistry:
    """Explicit canonical-resource lookup registry used only for export selection."""

    def __init__(self) -> None:
        self._sources: dict[str, _ExportSource] = {}

    def register(
        self,
        resource_type: str,
        loader: ExportLoader,
        *,
        exclusions: ExportExclusions | None = None,
    ) -> None:
        normalized = resource_type.strip()
        if not normalized:
            raise ValueError("export source resource_type must not be blank")
        if normalized in self._sources:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"portable export source already registered: {normalized}",
            )
        self._sources[normalized] = _ExportSource(loader=loader, exclusions=exclusions)

    async def load(self, selection: ExportSelection) -> tuple[object, tuple[ExcludedState, ...]]:
        try:
            source = self._sources[selection.resource_type]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"portable export source not registered: {selection.resource_type}",
            ) from exc
        value = await source.loader(selection.resource_id)
        exclusions = source.exclusions(value) if source.exclusions is not None else ()
        return value, exclusions

    def resource_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources))


@dataclass(frozen=True, slots=True)
class PackageInspection:
    package_id: str
    package: PortablePackage
    compatible: bool
    compatibility_issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoredImportPreview:
    preview_id: str
    package_id: str
    preview: ImportPreview
    compatible: bool
    compatibility_issues: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.compatible and self.preview.ready


@dataclass(frozen=True, slots=True)
class ImportReport:
    report_id: str
    package_id: str
    preview_id: str
    result: ImportExecutionResult


class PortabilityWorkflowService:
    """State-bound package -> preview -> import application service.

    Workflow records are intentionally operational coordination state, not a second
    representation of canonical imported resources. Package documents remain portable
    JSON; destination resources continue to be created only by registered mutation
    handlers through :class:`ImportExecutor`.
    """

    def __init__(
        self,
        *,
        serializers: ResourceSerializerRegistry,
        export_sources: ExportSourceRegistry,
        preview_service: ImportPreviewService,
        executor: ImportExecutor,
        platform_version: str,
        provenance_source: str = "control-plane",
        source_instance_id: str | None = None,
        export_compatibility: CompatibilityMetadata | None = None,
    ) -> None:
        if not platform_version.strip():
            raise ValueError("platform_version must not be blank")
        if not provenance_source.strip():
            raise ValueError("provenance_source must not be blank")
        self._serializers = serializers
        self._export_sources = export_sources
        self._preview_service = preview_service
        self._executor = executor
        self._platform_version = platform_version
        self._provenance_source = provenance_source
        self._source_instance_id = source_instance_id
        self._export_compatibility = export_compatibility or CompatibilityMetadata()
        self._packages: dict[str, PackageInspection] = {}
        self._previews: dict[str, StoredImportPreview] = {}
        self._reports: dict[str, ImportReport] = {}
        self._reports_by_preview: dict[str, ImportReport] = {}

    @property
    def export_resource_types(self) -> tuple[str, ...]:
        return self._export_sources.resource_types()

    async def export_package(
        self,
        selections: Iterable[ExportSelection],
        *,
        author: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> PackageInspection:
        ordered = tuple(sorted(selections, key=lambda item: (item.resource_type, item.resource_id)))
        identities = [(item.resource_type, item.resource_id) for item in ordered]
        if not ordered:
            raise ContractError(ErrorCode.INVALID_REQUEST, "portable export selection is empty")
        if len(identities) != len(set(identities)):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "portable export selection contains duplicate resources",
            )

        resources = []
        excluded_state: list[ExcludedState] = []
        for selection in ordered:
            value, exclusions = await self._export_sources.load(selection)
            resources.append(self._serializers.serialize(selection.resource_type, value))
            excluded_state.extend(exclusions)

        package = build_package(
            source_platform_version=self._platform_version,
            resources=tuple(resources),
            provenance=PackageProvenance(
                source=self._provenance_source,
                author=author,
                source_instance_id=self._source_instance_id,
                metadata=metadata or {},
            ),
            compatibility=self._export_compatibility,
            excluded_state=tuple(excluded_state),
        )
        return self._store_package(package)

    def validate_package_document(self, document: object) -> PackageInspection:
        """Parse, integrity-check, compatibility-check and register one package."""

        return self._store_package(package_from_dict(document))

    def package(self, package_id: str) -> PackageInspection:
        try:
            return self._packages[package_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"portable package not found: {package_id}",
            ) from exc

    def list_packages(self) -> tuple[PackageInspection, ...]:
        return tuple(self._packages[key] for key in sorted(self._packages))

    def preview_import(self, package_id: str) -> StoredImportPreview:
        inspection = self.package(package_id)
        preview = self._preview_service.preview(inspection.package)
        preview_id = _preview_id(package_id, preview, inspection.compatibility_issues)
        stored = StoredImportPreview(
            preview_id=preview_id,
            package_id=package_id,
            preview=preview,
            compatible=inspection.compatible,
            compatibility_issues=inspection.compatibility_issues,
        )
        self._previews[preview_id] = stored
        return stored

    def preview(self, preview_id: str) -> StoredImportPreview:
        try:
            return self._previews[preview_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"portable import preview not found: {preview_id}",
            ) from exc

    def list_previews(self) -> tuple[StoredImportPreview, ...]:
        return tuple(self._previews[key] for key in sorted(self._previews))

    async def execute_import(self, preview_id: str) -> ImportReport:
        completed = self._reports_by_preview.get(preview_id)
        if completed is not None:
            return completed
        stored = self.preview(preview_id)
        if not stored.compatible:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "portable package is incompatible with this platform version",
                details={"issues": list(stored.compatibility_issues)},
            )
        if not stored.preview.ready:
            raise ContractError(
                ErrorCode.CONFLICT,
                "portable import preview is not ready for mutation",
                details={
                    "conflict_count": len(stored.preview.conflicts),
                    "missing_dependency_count": len(stored.preview.missing_dependencies),
                    "blocking_security_finding_count": sum(
                        1 for item in stored.preview.security_findings if item.blocking
                    ),
                },
            )
        inspection = self.package(stored.package_id)
        result = await self._executor.execute(inspection.package, stored.preview)
        report_id = f"import_{_stable_digest(import_result_to_dict(result))}"
        report = ImportReport(
            report_id=report_id,
            package_id=stored.package_id,
            preview_id=stored.preview_id,
            result=result,
        )
        self._reports[report_id] = report
        self._reports_by_preview[stored.preview_id] = report
        return report

    def report(self, report_id: str) -> ImportReport:
        try:
            return self._reports[report_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"portable import report not found: {report_id}",
            ) from exc

    def list_reports(self) -> tuple[ImportReport, ...]:
        return tuple(self._reports[key] for key in sorted(self._reports))

    def _store_package(self, package: PortablePackage) -> PackageInspection:
        issues = _compatibility_issues(self._platform_version, package)
        package_id = f"package_{package.checksum}"
        inspection = PackageInspection(
            package_id=package_id,
            package=package,
            compatible=not issues,
            compatibility_issues=issues,
        )
        self._packages[package_id] = inspection
        return inspection


def package_inspection_to_dict(inspection: PackageInspection) -> dict[str, JsonValue]:
    return {
        "id": inspection.package_id,
        "package_id": inspection.package_id,
        "checksum": inspection.package.checksum,
        "compatible": inspection.compatible,
        "compatibility_issues": list(inspection.compatibility_issues),
        "resource_count": len(inspection.package.resources),
        "package": package_to_dict(inspection.package),
    }


def import_preview_to_dict(stored: StoredImportPreview) -> dict[str, JsonValue]:
    preview = stored.preview
    return {
        "id": stored.preview_id,
        "preview_id": stored.preview_id,
        "package_id": stored.package_id,
        "package_checksum": preview.package_checksum,
        "ready": stored.ready,
        "package_compatible": stored.compatible,
        "compatibility_issues": list(stored.compatibility_issues),
        "resources": [
            {
                "resource_type": item.resource_type,
                "source_id": item.source_id,
                "target_id": item.target_id,
                "resource_version": item.resource_version,
                "id_policy": item.id_policy.value,
            }
            for item in preview.resources
        ],
        "import_order": [
            {"resource_type": resource_type, "resource_id": resource_id}
            for resource_type, resource_id in preview.import_order
        ],
        "id_mapping": [
            {
                "resource_type": source[0],
                "source_id": source[1],
                "target_id": target_id,
            }
            for source, target_id in preview.id_mapping
        ],
        "missing_dependencies": [
            _missing_dependency_to_dict(item.requested_by, item.requirement)
            for item in preview.missing_dependencies
        ],
        "optional_missing_dependencies": [
            _missing_dependency_to_dict(item.requested_by, item.requirement)
            for item in preview.optional_missing_dependencies
        ],
        "conflicts": [
            {
                "kind": item.kind.value,
                "resource_type": item.resource_type,
                "resource_id": item.resource_id,
                "detail": item.detail,
            }
            for item in preview.conflicts
        ],
        "security_findings": [
            {
                "kind": item.kind.value,
                "resource_type": item.resource_type,
                "resource_id": item.resource_id,
                "detail": item.detail,
                "blocking": item.blocking,
            }
            for item in preview.security_findings
        ],
    }


def import_result_to_dict(result: ImportExecutionResult) -> dict[str, JsonValue]:
    return {
        "package_checksum": result.package_checksum,
        "resources": [
            {
                "resource_type": item.resource_type,
                "source_id": item.source_id,
                "target_id": item.target_id,
                "resource_version": item.resource_version,
            }
            for item in result.resources
        ],
    }


def import_report_to_dict(report: ImportReport) -> dict[str, JsonValue]:
    return {
        "id": report.report_id,
        "report_id": report.report_id,
        "package_id": report.package_id,
        "preview_id": report.preview_id,
        "status": "succeeded",
        "result": import_result_to_dict(report.result),
    }


def _missing_dependency_to_dict(
    requested_by: str,
    requirement: object,
) -> dict[str, JsonValue]:
    from .models import DependencyRequirement

    if not isinstance(requirement, DependencyRequirement):
        raise TypeError("requirement must be DependencyRequirement")
    return {
        "requested_by": requested_by,
        "kind": requirement.kind.value,
        "identifier": requirement.identifier,
        "required": requirement.required,
        "version_constraint": requirement.version_constraint,
        "purpose": requirement.purpose,
    }


def _compatibility_issues(platform_version: str, package: PortablePackage) -> tuple[str, ...]:
    compatibility = package.manifest.compatibility
    current = _release_version(platform_version, "target platform version")
    issues: list[str] = []
    if compatibility.minimum_platform_version is not None:
        minimum = _release_version(
            compatibility.minimum_platform_version,
            "minimum compatible platform version",
        )
        if current < minimum:
            issues.append(
                f"target platform {platform_version} is below required minimum "
                f"{compatibility.minimum_platform_version}"
            )
    if compatibility.maximum_platform_version is not None:
        maximum = _release_version(
            compatibility.maximum_platform_version,
            "maximum compatible platform version",
        )
        if current > maximum:
            issues.append(
                f"target platform {platform_version} is above supported maximum "
                f"{compatibility.maximum_platform_version}"
            )
    return tuple(issues)


def _release_version(value: str, label: str) -> tuple[int, ...]:
    if _RELEASE_VERSION.fullmatch(value) is None:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"{label} is not a supported numeric release version: {value}",
        )
    return tuple(int(part) for part in value.split("."))


def _preview_id(
    package_id: str,
    preview: ImportPreview,
    compatibility_issues: tuple[str, ...],
) -> str:
    document: dict[str, JsonValue] = {
        "package_id": package_id,
        "preview": import_preview_to_dict(
            StoredImportPreview(
                preview_id="preview_pending",
                package_id=package_id,
                preview=preview,
                compatible=not compatibility_issues,
                compatibility_issues=compatibility_issues,
            )
        ),
    }
    preview_document = document["preview"]
    if isinstance(preview_document, dict):
        preview_document.pop("id", None)
        preview_document.pop("preview_id", None)
    return f"preview_{_stable_digest(document)}"


def _stable_digest(document: dict[str, JsonValue]) -> str:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ExportSelection",
    "ExportSourceRegistry",
    "ImportReport",
    "PackageInspection",
    "PortabilityWorkflowService",
    "StoredImportPreview",
    "import_preview_to_dict",
    "import_report_to_dict",
    "package_inspection_to_dict",
]
