from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import cast

import pytest

from ai_multi_agent_platform.connectors import (
    Connection,
    ExternalNativeReference,
    ExternalResourceReference,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    AsyncSqliteRepositoryBindingCatalog,
    RepositoryBinding,
    RepositoryBindingRecord,
    RepositoryCallContext,
    RepositoryConnection,
    RepositoryManagementService,
    RepositoryProvider,
    RepositoryReference,
    RepositoryRegistry,
    RepositoryVisibility,
    SqliteRepositoryBindingCatalog,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


class _Provider:
    provider_id = "local-git"


def _reference(connection_id: str, native_id: str) -> RepositoryReference:
    return RepositoryReference(
        external_resource=ExternalResourceReference(
            id=new_id("external_resource"),
            connection_id=connection_id,
            resource_type="repository",
            native_reference=ExternalNativeReference(namespace="test", native_id=native_id),
        ),
        default_branch="main",
        visibility=RepositoryVisibility.LOCAL,
    )


def test_sqlite_cancellation_preserves_settled_worker_failure(tmp_path: Path) -> None:
    class FailingCatalog(SqliteRepositoryBindingCatalog):
        def __init__(self, path: Path) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            super().__init__(path)

        def save(self, record: RepositoryBindingRecord) -> RepositoryBindingRecord:
            del record
            self.started.set()
            if not self.release.wait(timeout=2):
                raise RuntimeError("test write was not released")
            raise sqlite3.OperationalError("disk I/O error")

    async def scenario() -> None:
        synchronous = FailingCatalog(tmp_path / "worker-failure.sqlite3")
        catalog = AsyncSqliteRepositoryBindingCatalog(synchronous)
        record = RepositoryBindingRecord(
            reference=_reference(new_id("connection"), "worker-failure"),
            provider_id="local-git",
            local=True,
        )

        save = asyncio.create_task(catalog.save(record))
        assert await asyncio.to_thread(synchronous.started.wait, 1)
        save.cancel()
        save.cancel()
        synchronous.release.set()

        with pytest.raises(ContractError) as failure:
            await save
        assert failure.value.code is ErrorCode.BACKEND_ERROR

    asyncio.run(scenario())


def test_management_cancellation_settles_persistence_and_registry_publish(tmp_path: Path) -> None:
    class BlockingCatalog(SqliteRepositoryBindingCatalog):
        def __init__(self, path: Path) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            super().__init__(path)

        def save(self, record: RepositoryBindingRecord) -> RepositoryBindingRecord:
            self.started.set()
            if not self.release.wait(timeout=2):
                raise RuntimeError("test write was not released")
            return super().save(record)

    async def scenario() -> None:
        owner_id = new_id("user")
        project_id = new_id("project")
        connection_id = new_id("connection")
        reference = _reference(connection_id, "cancelled-management-attach")
        connection = RepositoryConnection(
            connection=Connection(
                id=connection_id,
                connector_type_id="local-git",
                connector_version="1.0",
                owner_type="user",
                owner_id=owner_id,
                display_name="Cancellation repository",
                project_id=project_id,
            ),
            provider_id="local-git",
            local=True,
        )
        binding = RepositoryBinding(
            connection,
            reference,
            cast(RepositoryProvider, _Provider()),
        )
        registry = RepositoryRegistry()
        synchronous = BlockingCatalog(tmp_path / "management-cancel.sqlite3")
        authorization = AuthorizationGate(
            LocalAuthorizationProvider(
                (
                    LocalPrincipalPolicy(
                        principal_ref=owner_id,
                        actor_types=frozenset({ActorType.HUMAN}),
                        allowed_actions=frozenset({AuthorizationAction.CREATE}),
                        resource_types=frozenset({ResourceType.GENERIC}),
                        project_ids=frozenset({project_id}),
                    ),
                )
            )
        )
        service = RepositoryManagementService(
            registry,
            synchronous,
            authorization,
            managed_local_root=tmp_path / "managed",
        )
        context = RepositoryCallContext(
            operation=OperationContext(
                correlation_id="issue-892-management-cancellation",
                owner_type="user",
                owner_id=owner_id,
                project_id=project_id,
            ),
            actor_ref=owner_id,
        )

        attach = asyncio.create_task(service.attach_binding(binding, context))
        assert await asyncio.to_thread(synchronous.started.wait, 1)
        attach.cancel()
        attach.cancel()
        await asyncio.sleep(0.02)
        assert not attach.done()
        with pytest.raises(ContractError) as pending:
            registry.resolve(reference.id)
        assert pending.value.code is ErrorCode.NOT_FOUND

        synchronous.release.set()
        with pytest.raises(asyncio.CancelledError):
            await attach

        assert registry.resolve(reference.id) == binding
        assert synchronous.get(reference.id).reference == reference

    asyncio.run(scenario())
