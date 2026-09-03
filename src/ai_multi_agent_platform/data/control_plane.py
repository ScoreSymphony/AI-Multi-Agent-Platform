"""Read-only Control Plane projections for issue #13 data resources."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import validate_id

from .contracts import FileProvider
from .models import DataAccessContext, FileRecord
from .registry import DataProviderSet

DATA_PROVIDER_COLLECTION = "data-providers"
FILE_COLLECTION = "files"
ProjectIdProvider = Callable[[], tuple[str, ...]]


class DataProviderResourceService:
    """Administrative health/metadata inventory for File, Memory and Knowledge providers."""

    def __init__(self, providers: DataProviderSet) -> None:
        self._providers = providers
        _validate_unique_provider_ids(self._descriptors())

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _provider_resource(role, descriptor) for role, descriptor in self._descriptors()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        for role, descriptor in self._descriptors():
            if descriptor.provider_id == resource_id:
                return _provider_resource(role, descriptor)
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"data provider not found: {resource_id}",
        )

    def _descriptors(self) -> tuple[tuple[str, ProviderDescriptor], ...]:
        return (
            ("file", self._providers.files.descriptor),
            ("memory", self._providers.memory.descriptor),
            ("knowledge", self._providers.knowledge.descriptor),
        )


class FileResourceService:
    """Safe northbound metadata projection over the canonical #13 FileProvider.

    File bytes never enter this read model. Project-aware providers are enumerated once
    for the unscoped namespace and once for every canonical Project supplied by the
    composition root. Authorization-enforcing FileProvider decorators may reject an
    individual scope; such scopes are omitted rather than leaking their existence.
    """

    def __init__(
        self,
        files: FileProvider,
        *,
        project_ids: ProjectIdProvider | None = None,
    ) -> None:
        self._files = files
        self._project_ids = project_ids or (lambda: ())

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        requested_project = (query.filters or {}).get("project_id")
        if requested_project is not None:
            validate_id(requested_project, "project")
            scope_ids: tuple[str | None, ...] = (requested_project,)
        else:
            scope_ids = (None, *tuple(dict.fromkeys(self._project_ids())))

        resources: dict[str, dict[str, JsonValue]] = {}
        for project_id in scope_ids:
            try:
                records = await self._files.list_files(
                    _data_access_context(context, project_id=project_id)
                )
            except ContractError as exc:
                if exc.code in {ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                    continue
                raise
            for record in records:
                resources[record.file_id] = _file_resource(record)
        return tuple(resources[file_id] for file_id in sorted(resources))

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        validate_id(resource_id, "file")
        scope_ids: tuple[str | None, ...] = (None, *tuple(dict.fromkeys(self._project_ids())))
        for project_id in scope_ids:
            try:
                record = await self._files.get_file(
                    resource_id,
                    _data_access_context(context, project_id=project_id),
                )
            except ContractError as exc:
                if exc.code in {ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                    continue
                raise
            return _file_resource(record)
        raise ContractError(ErrorCode.NOT_FOUND, f"file not found: {resource_id}")


def data_resource_services(
    providers: DataProviderSet,
    *,
    project_ids: ProjectIdProvider | None = None,
) -> dict[str, DataProviderResourceService | FileResourceService]:
    """Register #13 provider inventory plus canonical File metadata discovery."""

    return {
        DATA_PROVIDER_COLLECTION: DataProviderResourceService(providers),
        FILE_COLLECTION: FileResourceService(providers.files, project_ids=project_ids),
    }


def _data_access_context(
    context: RequestContext,
    *,
    project_id: str | None,
) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id=context.correlation_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            project_id=project_id,
        ),
        actor_ref=context.actor.principal_ref,
    )


def _file_resource(record: FileRecord) -> dict[str, JsonValue]:
    return {
        "id": record.file_id,
        "type": "file",
        "project_id": record.project_id,
        "owner_ref": record.owner_ref,
        "created_by": record.created_by,
        "created_at": record.created_at.isoformat(),
        "size_bytes": record.size_bytes,
        "sha256": record.sha256,
        "state": record.state.value,
        "content_type": record.content_type,
        "artifact_ids": list(record.artifact_ids),
        "metadata": dict(record.metadata),
    }


def _provider_resource(role: str, descriptor: ProviderDescriptor) -> dict[str, JsonValue]:
    capabilities: list[JsonValue] = [
        _capability_resource(capability) for capability in descriptor.capabilities
    ]
    return {
        "id": descriptor.provider_id,
        "type": "data-provider",
        "role": role,
        "provider_type": descriptor.provider_type,
        "contract_version": descriptor.contract_version,
        "supported_operations": list(descriptor.supported_operations),
        "capabilities": capabilities,
        "health": descriptor.health.value,
        "available": descriptor.available,
        "limits": dict(descriptor.limits),
        "resources": dict(descriptor.resources),
    }


def _capability_resource(capability: Capability) -> dict[str, JsonValue]:
    return {
        "name": capability.name,
        "kind": capability.kind.value,
        "version": capability.version,
        "supported_operations": list(capability.supported_operations),
        "modalities": list(capability.modalities),
        "features": list(capability.features),
        "limits": dict(capability.limits),
        "attributes": dict(capability.attributes),
    }


def _validate_unique_provider_ids(
    descriptors: tuple[tuple[str, ProviderDescriptor], ...],
) -> None:
    provider_ids = [descriptor.provider_id for _, descriptor in descriptors]
    if len(set(provider_ids)) != len(provider_ids):
        raise ValueError("File, Memory and Knowledge providers must use unique provider IDs")
