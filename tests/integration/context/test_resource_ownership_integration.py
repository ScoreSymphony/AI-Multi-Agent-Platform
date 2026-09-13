from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.control_plane import ControlPlane, RequestContext
from ai_multi_agent_platform.control_plane.models import ActorContext, OwnerType
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


def _stack() -> tuple[ControlPlane, OrganizationService, InMemoryOrganizationRepository]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    organization_repository = InMemoryOrganizationRepository()
    organizations = OrganizationService(organization_repository)
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        authorization=FakeAuthorizationProvider(),
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


def test_project_and_workspace_owner_refs_are_mirrored_idempotently() -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack()
        organization = await organizations.create_organization(
            name="Mirror Org",
            owner_actor_id="user:owner",
        )
        team = await organizations.create_team(
            organization_id=organization.id,
            name="Platform",
        )
        project_context = _context(
            "user:owner",
            owner_type="team",
            owner_id=team.id,
            key="mirror-project",
        )
        project = await control_plane.create_project(
            project_context,
            {"name": "Team Project"},
        )
        project_id = project["id"]
        assert isinstance(project_id, str)
        project_ownership = await repository.get_ownership("project", project_id)
        assert project_ownership.owner_ref == OwnerRef(type="team", id=team.id)
        assert project_ownership.organization_id == organization.id

        replay = await control_plane.create_project(
            project_context,
            {"name": "Team Project"},
        )
        assert replay["id"] == project_id
        replay_ownership = await repository.get_ownership("project", project_id)
        assert replay_ownership.id == project_ownership.id

        workspace_context = _context(
            "user:owner",
            owner_type="team",
            owner_id=team.id,
            key="mirror-workspace",
        )
        workspace = await control_plane.create_workspace(
            workspace_context,
            {"project_id": project_id},
        )
        workspace_id = workspace["id"]
        assert isinstance(workspace_id, str)
        workspace_ownership = await repository.get_ownership("workspace", workspace_id)
        assert workspace_ownership.owner_ref == project_ownership.owner_ref
        assert workspace_ownership.organization_id == organization.id

    asyncio.run(scenario())


def test_personal_project_remains_first_class_without_organization() -> None:
    async def scenario() -> None:
        control_plane, _, repository = _stack()
        context = _context(
            "user:personal",
            owner_type="user",
            owner_id="personal",
            key="personal-project",
        )
        project = await control_plane.create_project(context, {"name": "Personal"})
        project_id = project["id"]
        assert isinstance(project_id, str)
        ownership = await repository.get_ownership("project", project_id)
        assert ownership.owner_ref == OwnerRef(type="user", id="personal")
        assert ownership.organization_id is None

    asyncio.run(scenario())


def test_mirror_detects_split_brain_instead_of_overwriting_canonical_owner() -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack()
        context = _context(
            "user:personal",
            owner_type="user",
            owner_id="personal",
            key="split-brain-project",
        )
        project = await control_plane.create_project(context, {"name": "Canonical"})
        project_id = project["id"]
        assert isinstance(project_id, str)
        original = await repository.get_ownership("project", project_id)
        await organizations.transfer_resource(
            resource_type="project",
            resource_id=project_id,
            new_owner_ref=OwnerRef(type="user", id="different"),
            organization_id=None,
        )

        with pytest.raises(ContractError, match="disagrees with the canonical resource owner"):
            await control_plane.create_project(context, {"name": "Canonical"})

        divergent = await repository.get_ownership("project", project_id)
        assert divergent.id == original.id
        assert divergent.owner_ref == OwnerRef(type="user", id="different")

    asyncio.run(scenario())


def test_generic_owner_mutation_is_rejected_for_mirrored_project() -> None:
    async def scenario() -> None:
        control_plane, _, _ = _stack()
        create_context = _context(
            "user:personal",
            owner_type="user",
            owner_id="personal",
            key="managed-project-create",
        )
        project = await control_plane.create_project(create_context, {"name": "Managed"})
        project_id = project["id"]
        assert isinstance(project_id, str)

        mutation_context = _context(
            "user:personal",
            owner_type="user",
            owner_id="personal",
            key="managed-project-transfer",
        )
        with pytest.raises(ContractError, match="managed by its canonical resource API"):
            await control_plane.execute_command(
                mutation_context,
                "resource-ownership.transfer",
                project_id,
                {
                    "resource_type": "project",
                    "resource_id": project_id,
                    "owner_ref": {"type": "user", "id": "different"},
                },
            )

    asyncio.run(scenario())
