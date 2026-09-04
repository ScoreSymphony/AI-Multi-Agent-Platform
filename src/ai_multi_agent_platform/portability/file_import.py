"""Portable File mutation handler for package-level import execution."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.data.contracts import FileProvider
from ai_multi_agent_platform.data.models import DataAccessContext

from .file_codecs import FILE_RESOURCE_TYPE, FilePortableSnapshot, materialize_file
from .models import PortableResource
from .registry import ImportContext


class FileImportMutationHandler:
    resource_type = FILE_RESOURCE_TYPE

    def __init__(self, provider: FileProvider, context: DataAccessContext) -> None:
        self._provider = provider
        self._context = context

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_file_snapshot(value)
        if snapshot.record.project_id != self._context.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "portable File scope does not match the destination import context",
                details={"file_id": snapshot.record.file_id},
            )
        try:
            await self._provider.get_file(snapshot.record.file_id, self._context)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        raise ContractError(
            ErrorCode.CONFLICT,
            "File appeared after import preview",
            details={"file_id": snapshot.record.file_id},
        )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_file_snapshot(value)
        record = await materialize_file(snapshot, self._provider, self._context)
        return record.file_id

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
                "portable File rollback token must be the imported File ID",
            )
        await self._provider.delete_file(token, self._context)


def _require_file_snapshot(value: object) -> FilePortableSnapshot:
    if not isinstance(value, FilePortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable File mutation handler received the wrong decoded resource type",
        )
    return value
