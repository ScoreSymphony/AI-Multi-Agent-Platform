"""File-provider ownership adapter for organization collaboration metadata."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    JsonValue,
    OperationContext,
    ProviderDescriptor,
    StoredObject,
)
from ai_multi_agent_platform.data.contracts import FileProvider
from ai_multi_agent_platform.data.models import DataAccessContext, FileRecord, OrphanReport
from ai_multi_agent_platform.data.registry import DataProviderSet
from ai_multi_agent_platform.domain import OwnerRef

from .service import OrganizationService


class OrganizationOwnershipFileProvider(FileProvider):
    """Mirror canonical File/Artifact ownership without changing FileProvider semantics."""

    def __init__(self, files: FileProvider, organizations: OrganizationService) -> None:
        self._files = files
        self._organizations = organizations

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._files.descriptor

    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        stored = await self._files.write(object_ref, data, context, metadata=metadata)
        access = DataAccessContext(operation=context, actor_ref=_operation_actor_ref(context))
        record = await self._files.get_file(object_ref, access)
        await self._mirror_file(record)
        return stored

    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        return await self._files.read(object_ref, context)

    async def create_file(
        self,
        data: bytes,
        context: DataAccessContext,
        *,
        file_id: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> FileRecord:
        record = await self._files.create_file(
            data,
            context,
            file_id=file_id,
            content_type=content_type,
            metadata=metadata,
        )
        await self._mirror_file(record)
        return record

    async def get_file(self, file_id: str, context: DataAccessContext) -> FileRecord:
        return await self._files.get_file(file_id, context)

    async def list_files(self, context: DataAccessContext) -> tuple[FileRecord, ...]:
        return await self._files.list_files(context)

    def stream_file(
        self,
        file_id: str,
        context: DataAccessContext,
        *,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        return self._files.stream_file(file_id, context, chunk_size=chunk_size)

    async def delete_file(self, file_id: str, context: DataAccessContext) -> FileRecord:
        return await self._files.delete_file(file_id, context)

    async def verify_checksum(self, file_id: str, context: DataAccessContext) -> bool:
        return await self._files.verify_checksum(file_id, context)

    async def link_artifact(
        self,
        file_id: str,
        artifact_id: str,
        context: DataAccessContext,
    ) -> FileRecord:
        record = await self._files.link_artifact(file_id, artifact_id, context)
        owner_ref, organization_id = await self._mirror_file(record)
        await self._mirror(
            resource_type="artifact",
            resource_id=artifact_id,
            owner_ref=owner_ref,
            organization_id=organization_id,
            actor_ref=context.actor_ref,
        )
        return record

    async def detect_orphans(self, context: DataAccessContext) -> OrphanReport:
        return await self._files.detect_orphans(context)

    async def _mirror_file(self, record: FileRecord) -> tuple[OwnerRef, str | None]:
        owner_ref = _owner_ref(record.owner_ref)
        organization_id = await self._organization_for_owner(owner_ref)
        await self._mirror(
            resource_type="file",
            resource_id=record.file_id,
            owner_ref=owner_ref,
            organization_id=organization_id,
            actor_ref=record.created_by,
        )
        return owner_ref, organization_id

    async def _organization_for_owner(self, owner_ref: OwnerRef) -> str | None:
        if owner_ref.type == "organization":
            await self._organizations.repository.get_organization(owner_ref.id)
            return owner_ref.id
        if owner_ref.type == "team":
            team = await self._organizations.repository.get_team(owner_ref.id)
            return team.organization_id
        return None

    async def _mirror(
        self,
        *,
        resource_type: str,
        resource_id: str,
        owner_ref: OwnerRef,
        organization_id: str | None,
        actor_ref: str,
    ) -> None:
        try:
            existing = await self._organizations.repository.get_ownership(resource_type, resource_id)
        except LookupError:
            await self._organizations.set_resource_owner(
                resource_type=resource_type,
                resource_id=resource_id,
                owner_ref=owner_ref,
                organization_id=organization_id,
                created_by_actor_id=actor_ref,
            )
            return
        if existing.owner_ref == owner_ref and existing.organization_id == organization_id:
            return
        raise ContractError(
            ErrorCode.CONFLICT,
            "file-backed ownership mirror disagrees with the canonical File owner",
            details={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "canonical_owner_type": owner_ref.type,
                "canonical_owner_id": owner_ref.id,
                "mirrored_owner_type": existing.owner_ref.type,
                "mirrored_owner_id": existing.owner_ref.id,
            },
        )


def with_organization_file_ownership(
    providers: DataProviderSet,
    organizations: OrganizationService,
) -> DataProviderSet:
    """Return the same provider bundle with File/Artifact ownership mirroring enabled."""

    if isinstance(providers.files, OrganizationOwnershipFileProvider):
        return providers
    return DataProviderSet(
        files=OrganizationOwnershipFileProvider(providers.files, organizations),
        memory=providers.memory,
        knowledge=providers.knowledge,
    )


def _operation_actor_ref(context: OperationContext) -> str:
    if context.owner_type is not None and context.owner_id is not None:
        return f"{context.owner_type}:{context.owner_id}"
    return "service:unspecified"


def _owner_ref(raw_owner: str) -> OwnerRef:
    if ":" not in raw_owner:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "canonical File owner_ref must use the platform owner-type:id form",
        )
    raw_type, raw_id = raw_owner.split(":", 1)
    if raw_type not in {"user", "organization", "team", "service"} or not raw_id:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "canonical File owner_ref is not a supported platform OwnerRef",
        )
    owner_type = cast(Literal["user", "organization", "team", "service"], raw_type)
    return OwnerRef(type=owner_type, id=raw_id)


__all__ = ["OrganizationOwnershipFileProvider", "with_organization_file_ownership"]
