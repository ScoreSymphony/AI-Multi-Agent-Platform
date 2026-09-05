"""Repository discovery, attachment and detachment lifecycle."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)

from .catalog import RepositoryBindingRecord, SqliteRepositoryBindingCatalog
from .contracts import RepositoryProvider
from .local_git import LocalGitRepositoryProvider
from .models import RepositoryConnection, RepositoryReference
from .service import RepositoryBinding, RepositoryCallContext, RepositoryRegistry

RepositoryDiscoveryResolver = Callable[
    [str, str], Awaitable[tuple[RepositoryConnection, RepositoryProvider]]
]

_MANAGED_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RepositoryManagementService:
    """Own durable repository attachment lifecycle without owning provider credentials."""

    def __init__(
        self,
        registry: RepositoryRegistry,
        catalog: SqliteRepositoryBindingCatalog,
        authorization: AuthorizationGate,
        *,
        managed_local_root: str | Path,
        discovery_resolver: RepositoryDiscoveryResolver | None = None,
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._authorization = authorization
        self._managed_local_root = Path(managed_local_root).expanduser().resolve()
        self._discovery_resolver = discovery_resolver

    async def attach_local(
        self,
        name: str,
        context: RepositoryCallContext,
        *,
        initialize: bool = False,
        default_branch: str = "main",
    ) -> RepositoryReference:
        """Attach or initialize one local repository below the deployment-managed root.

        The API deliberately accepts a managed directory name rather than an arbitrary host path.
        Raw filesystem paths remain adapter-private and are persisted only as provider bootstrap
        configuration, never as canonical repository identity.
        """

        managed_name = _managed_name(name)
        operation = context.operation
        if operation.owner_type is None or operation.owner_id is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "local repository attachment requires an explicit canonical owner",
            )
        if operation.project_id is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "local repository attachment requires a canonical project_id",
            )

        repository_id = new_id("external_resource")
        await self._enforce_management(
            repository_id,
            context,
            action=AuthorizationAction.CREATE,
            project_id=operation.project_id,
            side_effect="local_write",
            payload={"managed_name": managed_name, "initialize": initialize},
        )

        root = (self._managed_local_root / managed_name).resolve()
        try:
            root.relative_to(self._managed_local_root)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "managed repository name escapes the configured repository root",
            ) from exc

        connection = RepositoryConnection(
            connection=Connection(
                id=new_id("connection"),
                connector_type_id="local-git",
                connector_version="1.0",
                owner_type=operation.owner_type,
                owner_id=operation.owner_id,
                display_name=managed_name,
                project_id=operation.project_id,
            ),
            provider_id="local-git",
            local=True,
        )
        provider = LocalGitRepositoryProvider(root, connection)
        if initialize:
            reference = await provider.initialize(
                operation,
                default_branch=default_branch,
                repository_id=repository_id,
            )
        else:
            if not (root / ".git").is_dir():
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    f"managed local Git repository does not exist: {managed_name}",
                )
            discovered = await provider.open(operation)
            external = replace(discovered.external_resource, id=repository_id)
            reference = replace(discovered, external_resource=external)
            provider = LocalGitRepositoryProvider(
                root,
                connection,
                repository=reference,
            )

        binding = RepositoryBinding(connection, reference, provider)
        self._register_and_persist(
            binding,
            adapter_configuration={"root": str(root)},
        )
        return reference

    async def discover(
        self,
        connection_id: str,
        provider_id: str,
        context: RepositoryCallContext,
    ) -> tuple[RepositoryReference, ...]:
        """Discover repositories from a preconfigured hosted/self-hosted provider connection."""

        connection, provider = await self._resolve_discovery(connection_id, provider_id)
        operation = await self._enforce_management(
            connection_id,
            context,
            action=AuthorizationAction.READ,
            project_id=connection.connection.project_id,
            payload={"provider_id": provider_id},
        )
        return await provider.discover(connection, operation)

    async def discover_and_attach(
        self,
        connection_id: str,
        provider_id: str,
        context: RepositoryCallContext,
        *,
        adapter_configuration: Mapping[str, JsonValue] | None = None,
    ) -> tuple[RepositoryReference, ...]:
        """Discover and durably attach all repositories returned by one configured provider."""

        connection, provider = await self._resolve_discovery(connection_id, provider_id)
        operation = await self._enforce_management(
            connection_id,
            context,
            action=AuthorizationAction.READ,
            project_id=connection.connection.project_id,
            payload={"provider_id": provider_id, "attach": True},
        )
        references = await provider.discover(connection, operation)
        attached: list[RepositoryReference] = []
        for reference in references:
            await self.attach_binding(
                RepositoryBinding(connection, reference, provider),
                replace(context, operation=operation),
                adapter_configuration=adapter_configuration,
            )
            attached.append(reference)
        return tuple(attached)

    async def attach_binding(
        self,
        binding: RepositoryBinding,
        context: RepositoryCallContext,
        *,
        adapter_configuration: Mapping[str, JsonValue] | None = None,
    ) -> RepositoryReference:
        """Durably attach a provider-discovered canonical repository binding."""

        await self._enforce_management(
            binding.reference.id,
            context,
            action=AuthorizationAction.CREATE,
            project_id=binding.connection.connection.project_id,
            side_effect="local_write",
            payload={
                "connection_id": binding.connection.id,
                "provider_id": binding.provider.provider_id,
            },
        )
        self._register_and_persist(
            binding,
            adapter_configuration=adapter_configuration or {},
        )
        return binding.reference

    async def detach(
        self,
        repository_id: str,
        context: RepositoryCallContext,
    ) -> RepositoryReference:
        """Detach routing/persistence only; never delete the provider-owned repository itself."""

        binding = self._registry.resolve(repository_id)
        await self._enforce_management(
            repository_id,
            context,
            action=AuthorizationAction.DELETE,
            project_id=binding.connection.connection.project_id,
            side_effect="local_write",
            payload={"delete_provider_content": False},
        )
        self._catalog.delete(repository_id)
        self._registry.unregister(repository_id)
        return binding.reference

    async def _resolve_discovery(
        self,
        connection_id: str,
        provider_id: str,
    ) -> tuple[RepositoryConnection, RepositoryProvider]:
        resolver = self._discovery_resolver
        if resolver is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "repository provider discovery is not configured for this deployment",
                retryable=True,
            )
        connection, provider = await resolver(connection_id, provider_id)
        if connection.id != connection_id or connection.provider_id != provider_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository discovery resolver returned the wrong connection/provider binding",
            )
        if provider.provider_id != provider_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository discovery resolver returned a provider with the wrong provider_id",
            )
        return connection, provider

    def _register_and_persist(
        self,
        binding: RepositoryBinding,
        *,
        adapter_configuration: Mapping[str, JsonValue],
    ) -> None:
        self._registry.register(binding)
        try:
            self._catalog.save(
                RepositoryBindingRecord.from_binding(
                    binding,
                    adapter_configuration=adapter_configuration,
                )
            )
        except Exception:
            self._registry.unregister(binding.reference.id)
            raise

    async def _enforce_management(
        self,
        resource_id: str,
        context: RepositoryCallContext,
        *,
        action: AuthorizationAction,
        project_id: str | None = None,
        side_effect: str | None = None,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> OperationContext:
        requested_project_id = context.operation.project_id
        if (
            project_id is not None
            and requested_project_id is not None
            and project_id != requested_project_id
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "repository management project scope does not match configured connection",
            )
        operation = (
            replace(context.operation, project_id=project_id)
            if project_id is not None and requested_project_id is None
            else context.operation
        )
        proposed = ProposedAction(
            AuthorizationContext(
                actor=infer_actor_identity(context.actor_ref),
                action=action,
                resource_type=ResourceType.GENERIC,
                resource_id=resource_id,
                operation=operation,
                task_id=context.task_id,
                run_id=context.run_id,
                agent_id=context.agent_id,
                side_effect=side_effect,
                security_labels=("repository", "repository.management"),
            ),
            payload=dict(payload or {}),
        )
        await self._authorization.enforce(
            proposed,
            approval_id=context.approval_id,
            risk=(
                RiskClassification.ELEVATED
                if action in {AuthorizationAction.CREATE, AuthorizationAction.DELETE}
                else RiskClassification.STANDARD
            ),
        )
        return operation


def _managed_name(value: str) -> str:
    name = value.strip()
    if name in {".", ".."} or _MANAGED_NAME.fullmatch(name) is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "managed repository name must use only letters, digits, dot, underscore or hyphen",
        )
    return name
