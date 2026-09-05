from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.data.models import DataAccessContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    OrganizationOwnershipFileProvider,
    OrganizationService,
)


def test_file_write_and_artifact_link_mirror_personal_canonical_owner(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(repository)
        files = OrganizationOwnershipFileProvider(
            LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
            organizations,
        )
        operation = OperationContext(
            correlation_id="correlation-file-owner",
            owner_type="user",
            owner_id="alice",
        )
        file_id = "file_issue87_owner_adapter"

        stored = await files.write(file_id, b"payload", operation)
        assert stored.object_ref == file_id
        ownership = await repository.get_ownership("file", file_id)
        assert ownership.owner_ref == OwnerRef(type="user", id="alice")
        assert ownership.organization_id is None

        access = DataAccessContext(operation=operation, actor_ref="user:alice")
        artifact_id = "artifact_issue87_owner_adapter"
        linked = await files.link_artifact(file_id, artifact_id, access)
        assert artifact_id in linked.artifact_ids
        artifact_ownership = await repository.get_ownership("artifact", artifact_id)
        assert artifact_ownership.owner_ref == ownership.owner_ref
        assert artifact_ownership.organization_id is None

        reread = await files.get_file(file_id, access)
        assert reread.file_id == file_id
        assert (await repository.get_ownership("file", file_id)).id == ownership.id

    asyncio.run(scenario())


def test_file_create_mirrors_organization_owner_without_read_side_effects(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(repository)
        organization = await organizations.create_organization(
            name="File Org",
            owner_actor_id="user:owner",
        )
        files = OrganizationOwnershipFileProvider(
            LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
            organizations,
        )
        operation = OperationContext(
            correlation_id="correlation-org-file",
            owner_type="organization",
            owner_id=organization.id,
        )
        access = DataAccessContext(
            operation=operation,
            actor_ref=f"organization:{organization.id}",
        )

        record = await files.create_file(
            b"organization payload",
            access,
            file_id="file_issue87_org_owner",
        )
        ownership = await repository.get_ownership("file", record.file_id)
        assert ownership.owner_ref == OwnerRef(type="organization", id=organization.id)
        assert ownership.organization_id == organization.id

        before = tuple(await repository.list_ownerships())
        await files.get_file(record.file_id, access)
        after = tuple(await repository.list_ownerships())
        assert after == before

    asyncio.run(scenario())
