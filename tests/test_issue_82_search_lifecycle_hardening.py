from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorRegistry,
    ConnectorService,
    ExternalNativeReference,
    ExternalResourceReference,
    InMemoryConnectorRepository,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.repositories import (
    RepositoryBinding,
    RepositoryBindingRecord,
    RepositoryConnection,
    RepositoryManagementService,
    RepositoryProvenanceStore,
    RepositoryReference,
    RepositoryRegistry,
    RepositoryRunProvenance,
    RepositoryService,
    RepositoryVisibility,
    SqliteRepositoryBindingCatalog,
)
from ai_multi_agent_platform.repositories.connector_bootstrap import restore_connector_repositories
from ai_multi_agent_platform.repositories.control_plane import (
    RepositoryResourceService,
    register_repository_control_plane,
)
from ai_multi_agent_platform.search import document_from_resource
from ai_multi_agent_platform.security import AuthorizationGate, LocalAuthorizationProvider
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class _RepositoryProviderStub:
    provider_id = "repository-reference"


def _connection(project_id: str) -> Connection:
    return Connection(
        id=new_id("connection"),
        connector_type_id="reference.local",
        connector_version="1.0",
        owner_type="user",
        owner_id="repository-owner",
        display_name="Repository connection",
        project_id=project_id,
    )


def _binding(connection: Connection) -> RepositoryBinding:
    repository_connection = RepositoryConnection(
        connection=connection,
        provider_id="repository-reference",
    )
    reference = RepositoryReference(
        external_resource=ExternalResourceReference(
            id=new_id("external_resource"),
            connection_id=connection.id,
            resource_type="repository",
            native_reference=ExternalNativeReference("reference.local", "provider-native-82"),
            canonical_url="https://git.example/private/repository.git",
            revision="a" * 40,
        ),
        default_branch="main",
        target_revision="refs/heads/main",
        resolved_revision="a" * 40,
        visibility=RepositoryVisibility.PRIVATE,
    )
    return RepositoryBinding(
        repository_connection,
        reference,
        _RepositoryProviderStub(),  # type: ignore[arg-type]
    )


def _headers(idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Principal-Ref": "user:repository-owner",
        "X-Owner-Type": "user",
        "X-Owner-Id": "repository-owner",
        "X-Correlation-Id": "issue-82-search-lifecycle",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _control_plane() -> ControlPlane:
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    return ControlPlane(kernel=kernel, events=events)


def test_repository_search_preserves_connection_scope_without_becoming_provenance_authority() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        connection = _connection(project_id)
        binding = _binding(connection)
        registry = RepositoryRegistry()
        registry.register(binding)
        service = RepositoryService(
            registry,
            AuthorizationGate(LocalAuthorizationProvider(())),
        )
        resources = RepositoryResourceService(service)
        provenance = RepositoryProvenanceStore()
        run_id = new_id("run")
        input_revision = "1" * 40
        output_revision = "2" * 40
        record = RepositoryRunProvenance(
            run_id=run_id,
            repository_id=binding.reference.id,
            input_revision=input_revision,
            output_revision=output_revision,
            actor_ref="user:repository-owner",
        )
        provenance.record(record)

        projected = await resources.list_search_resources()
        assert len(projected) == 1
        assert projected[0]["connection_id"] == connection.id
        document = document_from_resource(projected[0], collection="repositories")
        assert connection.id in document.keywords
        assert document.project_id == project_id
        # A Repository can back multiple Workspaces. Search must not invent one Workspace scope.
        assert document.workspace_id is None
        # Run input/output provenance remains canonical Repository provenance, not derived Search state.
        assert provenance.get(run_id, binding.reference.id) == record
        serialized = repr(document)
        assert input_revision not in serialized
        assert output_revision not in serialized

    asyncio.run(scenario())


def test_connection_remove_prunes_repository_catalog_registry_and_search(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        connection = _connection(project_id)
        binding = _binding(connection)

        connector_repository = InMemoryConnectorRepository()
        await connector_repository.save_connection(connection)
        connectors = ConnectorService(connector_repository, ConnectorRegistry())

        registry = RepositoryRegistry()
        registry.register(binding)
        catalog = SqliteRepositoryBindingCatalog(tmp_path / "repository-bindings.sqlite3")
        catalog.save(RepositoryBindingRecord.from_binding(binding))
        service = RepositoryService(
            registry,
            AuthorizationGate(LocalAuthorizationProvider(())),
        )
        management = RepositoryManagementService(
            registry,
            catalog,
            AuthorizationGate(LocalAuthorizationProvider(())),
            managed_local_root=tmp_path / "managed",
        )
        resources = RepositoryResourceService(service)

        control_plane = _control_plane()
        register_repository_control_plane(control_plane, service, management=management)
        register_connector_control_plane(control_plane, connectors)
        http = ControlPlaneHTTP(control_plane)

        assert len(await resources.list_search_resources()) == 1
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/connection.remove",
                headers=_headers("issue-82-remove-connection"),
                body={"resource_ref": connection.id},
            )
        )
        assert response.status == 200

        with pytest.raises(ContractError) as connector_exc:
            await connector_repository.get_connection(connection.id)
        assert connector_exc.value.code is ErrorCode.NOT_FOUND
        with pytest.raises(ContractError) as registry_exc:
            registry.resolve(binding.reference.id)
        assert registry_exc.value.code is ErrorCode.NOT_FOUND
        with pytest.raises(ContractError) as catalog_exc:
            catalog.get(binding.reference.id)
        assert catalog_exc.value.code is ErrorCode.NOT_FOUND
        assert await resources.list_search_resources() == ()

    asyncio.run(scenario())


def test_connector_repository_restore_prunes_binding_whose_connection_was_removed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        connection = _connection(new_id("project"))
        binding = _binding(connection)
        catalog = SqliteRepositoryBindingCatalog(tmp_path / "repository-bindings.sqlite3")
        catalog.save(RepositoryBindingRecord.from_binding(binding))
        registry = RepositoryRegistry()

        restored = await restore_connector_repositories(
            catalog,
            registry,
            InMemoryConnectorRepository(),
            ConnectorRegistry(),
        )

        assert restored == ()
        assert catalog.list() == ()
        assert registry.list() == ()

    asyncio.run(scenario())
