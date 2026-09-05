from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.connectors import (
    Connection,
    ExternalNativeReference,
    ExternalResourceReference,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    RepositoryBinding,
    RepositoryConnection,
    RepositoryOperation,
    RepositoryReference,
    RepositoryRegistry,
    RepositoryService,
    RepositoryVisibility,
    repository_capability,
)
from ai_multi_agent_platform.repositories.control_plane import RepositoryResourceService
from ai_multi_agent_platform.search import document_from_resource
from ai_multi_agent_platform.security import AuthorizationGate, LocalAuthorizationProvider


class _RepositoryProviderStub:
    provider_id = "repository-github"


def _fixture() -> tuple[
    RepositoryRegistry,
    RepositoryResourceService,
    RepositoryBinding,
    str,
]:
    project_id = new_id("project")
    connection = RepositoryConnection(
        connection=Connection(
            id=new_id("connection"),
            connector_type_id="github",
            connector_version="1.0",
            owner_type="user",
            owner_id="repository-owner",
            display_name="GitHub connection",
            project_id=project_id,
        ),
        provider_id="repository-github",
    )
    native_id = "provider-native-repository-987654321"
    revision = "a" * 40
    reference = RepositoryReference(
        external_resource=ExternalResourceReference(
            id=new_id("external_resource"),
            connection_id=connection.id,
            resource_type="repository",
            native_reference=ExternalNativeReference("github", native_id),
            canonical_url="https://github.example/private-org/searchable-repository.git",
            revision=revision,
            metadata={
                "provider_private_payload": "must-never-enter-search",
                "clone_path": "/srv/private/provider/clone",
            },
        ),
        default_branch="main",
        target_revision="refs/heads/main",
        resolved_revision=revision,
        visibility=RepositoryVisibility.PRIVATE,
        capabilities=(
            repository_capability(RepositoryOperation.READ),
            repository_capability(RepositoryOperation.INSPECT_REFS),
        ),
        metadata={"local_clone_path": "/srv/private/local/clone"},
    )
    provider = _RepositoryProviderStub()
    binding = RepositoryBinding(connection, reference, provider)  # type: ignore[arg-type]
    registry = RepositoryRegistry()
    registry.register(binding)
    repositories = RepositoryService(
        registry,
        AuthorizationGate(LocalAuthorizationProvider(())),
    )
    return registry, RepositoryResourceService(repositories), binding, native_id


def test_repository_search_projection_is_safe_searchable_and_reconstructable() -> None:
    async def scenario() -> None:
        registry, resources, binding, native_id = _fixture()

        searchable = await resources.list_search_resources()
        assert len(searchable) == 1
        resource = searchable[0]
        assert resource["id"] == binding.reference.id
        assert resource["type"] == "repository"
        assert resource["name"] == "searchable-repository"
        assert resource["project_id"] == binding.connection.connection.project_id
        assert resource["owner_type"] == "user"
        assert resource["owner_id"] == "repository-owner"
        assert resource["status"] == "private"
        assert resource["revision"] == "a" * 40
        assert resource["aliases"] == [
            "github.example",
            "main",
            "refs/heads/main",
            "a" * 40,
        ]
        assert resource["capabilities"] == [
            "repository.read",
            "repository.inspect_refs",
        ]

        serialized = repr(resource)
        assert native_id not in serialized
        assert "private-org" not in serialized
        assert "must-never-enter-search" not in serialized
        assert "/srv/private" not in serialized

        document = document_from_resource(resource, collection="repositories")
        assert document.resource_type == "repository"
        assert document.resource_id == binding.reference.id
        assert document.title == "searchable-repository"
        assert document.project_id == binding.connection.connection.project_id
        assert document.version == "a" * 40
        assert document.canonical_ref == f"/api/v1/repositories/{binding.reference.id}"
        assert "github.example" in document.keywords
        assert "main" in document.keywords
        assert "refs/heads/main" in document.keywords
        assert "repository.inspect_refs" in document.keywords

        updated_reference = replace(binding.reference, resolved_revision="b" * 40)
        registry.replace(
            RepositoryBinding(
                binding.connection,
                updated_reference,
                binding.provider,
            )
        )
        updated = await resources.list_search_resources()
        assert len(updated) == 1
        assert updated[0]["revision"] == "b" * 40
        assert "b" * 40 in updated[0]["aliases"]

        registry.unregister(binding.reference.id)
        assert await resources.list_search_resources() == ()

    asyncio.run(scenario())
