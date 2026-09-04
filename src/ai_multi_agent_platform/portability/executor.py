"""Rollback-safe package import execution for issue #79."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import redact_exception

from .models import PortablePackage, PortableResource
from .package import verify_package
from .planner import ImportPreview
from .registry import ImportContext, ResourceSerializerRegistry


class ImportMutationHandler(Protocol):
    """Mutation boundary for one portable resource type.

    ``preflight`` must not mutate destination state. ``apply`` must either return a
    rollback token after a complete resource mutation or compensate its own partial
    mutation before raising. ``rollback`` must undo a previously successful ``apply``.
    """

    @property
    def resource_type(self) -> str: ...

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None: ...

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object: ...

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None: ...


class ImportMutationRegistry:
    """Explicit mutation-handler registry; codecs alone never grant write authority."""

    def __init__(self) -> None:
        self._handlers: dict[str, ImportMutationHandler] = {}

    def register(self, handler: ImportMutationHandler) -> None:
        resource_type = handler.resource_type.strip()
        if not resource_type:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "portable import mutation handler type must not be blank",
            )
        if resource_type in self._handlers:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"portable import mutation handler already registered: {resource_type}",
            )
        self._handlers[resource_type] = handler

    def get(self, resource_type: str) -> ImportMutationHandler:
        try:
            return self._handlers[resource_type]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"portable import mutation handler not registered: {resource_type}",
            ) from exc

    def resource_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


@dataclass(frozen=True, slots=True)
class AppliedImportResource:
    resource_type: str
    source_id: str
    target_id: str
    resource_version: str


@dataclass(frozen=True, slots=True)
class ImportExecutionResult:
    package_checksum: str
    resources: tuple[AppliedImportResource, ...]


@dataclass(frozen=True, slots=True)
class _PreparedResource:
    resource: PortableResource
    value: object
    handler: ImportMutationHandler


@dataclass(frozen=True, slots=True)
class _AppliedResource:
    prepared: _PreparedResource
    token: object


class ImportExecutor:
    """Execute only an accepted preview and compensate package mutations on failure."""

    def __init__(
        self,
        serializers: ResourceSerializerRegistry,
        mutations: ImportMutationRegistry,
    ) -> None:
        self._serializers = serializers
        self._mutations = mutations

    async def execute(
        self,
        package: PortablePackage,
        preview: ImportPreview,
    ) -> ImportExecutionResult:
        """Preflight every resource before applying any destination mutation."""

        verify_package(package)
        _validate_preview(package, preview)
        context = ImportContext(id_mapping=preview.mapping_dict())
        resources_by_key = {
            (resource.resource_type, resource.resource_id): resource
            for resource in package.resources
        }

        prepared: list[_PreparedResource] = []
        for key in preview.import_order:
            resource = resources_by_key[key]
            handler = self._mutations.get(resource.resource_type)
            value = self._serializers.deserialize(resource, context)
            prepared.append(_PreparedResource(resource=resource, value=value, handler=handler))

        for item in prepared:
            await item.handler.preflight(item.resource, item.value, context)

        applied: list[_AppliedResource] = []
        try:
            for item in prepared:
                token = await item.handler.apply(item.resource, item.value, context)
                applied.append(_AppliedResource(prepared=item, token=token))
        except Exception as exc:
            await self._rollback_after_failure(applied, context, exc)
            raise AssertionError("rollback helper must always raise") from exc

        mapping = preview.mapping_dict()
        return ImportExecutionResult(
            package_checksum=package.checksum,
            resources=tuple(
                AppliedImportResource(
                    resource_type=item.resource.resource_type,
                    source_id=item.resource.resource_id,
                    target_id=mapping[(item.resource.resource_type, item.resource.resource_id)],
                    resource_version=item.resource.resource_version,
                )
                for item in prepared
            ),
        )

    async def _rollback_after_failure(
        self,
        applied: list[_AppliedResource],
        context: ImportContext,
        error: Exception,
    ) -> None:
        rollback_failures: list[JsonValue] = []
        for item in reversed(applied):
            try:
                await item.prepared.handler.rollback(
                    item.prepared.resource,
                    item.prepared.value,
                    item.token,
                    context,
                )
            except Exception as rollback_error:
                rollback_failures.append(redact_exception(rollback_error))

        original = redact_exception(error)
        if rollback_failures:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "portable import failed and package rollback was incomplete",
                details={
                    "original_error": original,
                    "rollback_complete": False,
                    "rollback_failures": rollback_failures,
                    "applied_resource_count": len(applied),
                },
            ) from error

        code = error.code if isinstance(error, ContractError) else ErrorCode.BACKEND_ERROR
        raise ContractError(
            code,
            "portable import failed; applied package changes were rolled back",
            details={
                "original_error": original,
                "rollback_complete": True,
                "applied_resource_count": len(applied),
            },
        ) from error


def _validate_preview(package: PortablePackage, preview: ImportPreview) -> None:
    if preview.package_checksum != package.checksum:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable import preview does not belong to this package checksum",
        )
    if not preview.ready or preview.conflicts or preview.missing_dependencies:
        raise ContractError(
            ErrorCode.CONFLICT,
            "portable import preview is not ready for mutation",
            details={
                "conflict_count": len(preview.conflicts),
                "missing_dependency_count": len(preview.missing_dependencies),
            },
        )

    package_keys = [(item.resource_type, item.resource_id) for item in package.resources]
    if len(package_keys) != len(set(package_keys)):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable package contains duplicate canonical resource identities",
        )
    if len(preview.import_order) != len(set(preview.import_order)):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable import preview order contains duplicate resources",
        )
    if set(preview.import_order) != set(package_keys):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable import preview order does not cover the package exactly once",
        )

    mapping = preview.mapping_dict()
    if set(mapping) != set(package_keys):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable import preview ID mapping does not cover the package exactly once",
        )

    planned = {(item.resource_type, item.source_id): item for item in preview.resources}
    if set(planned) != set(package_keys):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable import preview resource plan does not cover the package exactly once",
        )
    for source_key, item in planned.items():
        if mapping[source_key] != item.target_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable import preview target ID disagrees with its ID mapping",
                details={"resource_type": source_key[0], "resource_id": source_key[1]},
            )
