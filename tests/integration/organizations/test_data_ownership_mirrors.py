from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ControlPlane, RequestContext
from ai_multi_agent_platform.control_plane.models import ActorContext, OwnerType
from ai_multi_agent_platform.data import (
    DataProviderSet,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
)
from ai_multi_agent_platform.data.lifecycle_commands import data_command_handlers
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    OrganizationService,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _stack(
    tmp_path: Path,
) -> tuple[ControlPlane, OrganizationService, InMemoryOrganizationRepository]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    providers = DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )
    organization_repository = InMemoryOrganizationRepository()
    organizations = OrganizationService(organization_repository)
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        authorization=FakeAuthorizationProvider(),
        command_handlers=data_command_handlers(providers),
        organization_service=organizations,
    )
    return control_plane, organizations, organization_repository


def _context(
    principal: str,
    *,
    owner_type: OwnerType,
    owner_id: str,
    key: str,
) -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        actor=ActorContext(
            principal_ref=principal,
            owner_type=owner_type,
            owner_id=owner_id,
        ),
        idempotency_key=key,
    )


def test_organization_memory_create_update_and_promote_are_strictly_mirrored(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack(tmp_path)
        organization = await organizations.create_organization(
            name="Memory Org",
            owner_actor_id="user:owner",
        )
        organization_context = _context(
            "user:owner",
            owner_type="organization",
            owner_id=organization.id,
            key="memory-org-create",
        )

        created = await control_plane.execute_command(
            organization_context,
            "memory.create",
            organization.id,
            {
                "scope": "organization",
                "origin": "user-authored",
                "value": {"kind": "organization-memory"},
            },
        )
        memory_id = created["id"]
        assert isinstance(memory_id, str)
        ownership = await repository.get_ownership("memory", memory_id)
        assert ownership.owner_ref == OwnerRef(type="organization", id=organization.id)
        assert ownership.organization_id == organization.id

        updated = await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="organization",
                owner_id=organization.id,
                key="memory-org-update",
            ),
            "memory.update",
            memory_id,
            {"value": {"kind": "updated-organization-memory"}},
        )
        updated_memory_id = updated["id"]
        assert isinstance(updated_memory_id, str)
        assert updated_memory_id != memory_id
        updated_ownership = await repository.get_ownership("memory", updated_memory_id)
        assert updated_ownership.owner_ref == OwnerRef(type="organization", id=organization.id)
        assert updated_ownership.organization_id == organization.id

        short_term = await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="user",
                owner_id="owner",
                key="memory-short-term",
            ),
            "memory.create",
            "session-owner-transfer",
            {
                "scope": "short_term",
                "origin": "user-authored",
                "value": {"kind": "temporary"},
            },
        )
        short_term_id = short_term["id"]
        assert isinstance(short_term_id, str)
        temporary_ownership = await repository.get_ownership("memory", short_term_id)
        assert temporary_ownership.owner_ref == OwnerRef(type="user", id="owner")
        assert temporary_ownership.organization_id is None

        promoted = await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="organization",
                owner_id=organization.id,
                key="memory-promote-org",
            ),
            "memory.promote",
            short_term_id,
            {
                "scope": "organization",
                "scope_id": organization.id,
            },
        )
        promoted_id = promoted["id"]
        assert isinstance(promoted_id, str)
        promoted_ownership = await repository.get_ownership("memory", promoted_id)
        assert promoted_ownership.owner_ref == OwnerRef(type="organization", id=organization.id)
        assert promoted_ownership.organization_id == organization.id

        with pytest.raises(ContractError) as direct_transfer:
            await control_plane.execute_command(
                organization_context,
                "resource-ownership.transfer",
                promoted_id,
                {
                    "resource_type": "memory",
                    "resource_id": promoted_id,
                    "owner_ref": {"type": "user", "id": "other"},
                },
            )
        assert direct_transfer.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_knowledge_register_and_update_mirror_canonical_personal_owner(tmp_path: Path) -> None:
    async def scenario() -> None:
        control_plane, _, repository = _stack(tmp_path)
        context = _context(
            "user:owner",
            owner_type="user",
            owner_id="owner",
            key="knowledge-register",
        )

        registered = await control_plane.execute_command(
            context,
            "knowledge.register",
            "user:owner",
            {
                "title": "Personal source",
            },
        )
        source_id = registered["id"]
        assert isinstance(source_id, str)
        ownership = await repository.get_ownership("knowledge_source", source_id)
        assert ownership.owner_ref == OwnerRef(type="user", id="owner")
        assert ownership.organization_id is None

        updated = await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="user",
                owner_id="owner",
                key="knowledge-update",
            ),
            "knowledge.update",
            source_id,
            {"title": "Updated source"},
        )
        assert updated["id"] == source_id
        replayed_ownership = await repository.get_ownership("knowledge_source", source_id)
        assert replayed_ownership.id == ownership.id
        assert replayed_ownership.owner_ref == OwnerRef(type="user", id="owner")

        with pytest.raises(ContractError) as direct_transfer:
            await control_plane.execute_command(
                context,
                "resource-ownership.transfer",
                source_id,
                {
                    "resource_type": "knowledge_source",
                    "resource_id": source_id,
                    "owner_ref": {"type": "organization", "id": "org_unknown"},
                },
            )
        assert direct_transfer.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())
