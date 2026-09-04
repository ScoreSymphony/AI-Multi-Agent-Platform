"""Repository routing, authorization gates and run provenance recording."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.security import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)

from .contracts import RepositoryProvider
from .models import (
    RepositoryCommit,
    RepositoryConnection,
    RepositoryDiff,
    RepositoryOperation,
    RepositoryReference,
    RepositoryRevision,
    RepositoryRunProvenance,
    RepositoryStatus,
)


@dataclass(frozen=True, slots=True)
class RepositoryBinding:
    connection: RepositoryConnection
    reference: RepositoryReference
    provider: RepositoryProvider

    def __post_init__(self) -> None:
        if self.connection.id != self.reference.connection_id:
            raise ValueError("repository binding connection/reference mismatch")
        if self.connection.provider_id != self.provider.provider_id:
            raise ValueError("repository binding provider mismatch")


class RepositoryRegistry:
    """Route canonical repository IDs to replaceable providers without exposing provider types."""

    def __init__(self) -> None:
        self._bindings: dict[str, RepositoryBinding] = {}

    def register(self, binding: RepositoryBinding) -> None:
        if binding.reference.id in self._bindings:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"repository already registered: {binding.reference.id}",
            )
        self._bindings[binding.reference.id] = binding

    def replace(self, binding: RepositoryBinding) -> None:
        current = self._bindings.get(binding.reference.id)
        if current is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"repository not registered: {binding.reference.id}",
            )
        if current.reference.connection_id != binding.reference.connection_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "provider replacement cannot change canonical repository connection identity",
            )
        self._bindings[binding.reference.id] = binding

    def resolve(self, repository_id: str) -> RepositoryBinding:
        binding = self._bindings.get(repository_id)
        if binding is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"repository not found: {repository_id}")
        return binding

    def list(self, *, connection_id: str | None = None) -> tuple[RepositoryBinding, ...]:
        values = tuple(self._bindings.values())
        if connection_id is None:
            return values
        return tuple(value for value in values if value.reference.connection_id == connection_id)


@dataclass(frozen=True, slots=True)
class RepositoryCallContext:
    operation: OperationContext
    actor_ref: str
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if not self.actor_ref.strip():
            raise ValueError("repository actor_ref must not be blank")


class RepositoryService:
    """Policy-enforced repository facade; callers do not invoke provider binaries directly."""

    def __init__(self, registry: RepositoryRegistry, authorization: AuthorizationGate) -> None:
        self._registry = registry
        self._authorization = authorization

    async def status(
        self, repository_id: str, context: RepositoryCallContext
    ) -> RepositoryStatus:
        binding = self._registry.resolve(repository_id)
        await self._enforce(binding.reference, RepositoryOperation.STATUS, context)
        return await binding.provider.status(binding.reference, context.operation)

    async def diff(
        self,
        repository_id: str,
        context: RepositoryCallContext,
        *,
        base_revision: str | None = None,
    ) -> RepositoryDiff:
        binding = self._registry.resolve(repository_id)
        await self._enforce(binding.reference, RepositoryOperation.DIFF, context)
        return await binding.provider.diff(
            binding.reference,
            context.operation,
            base_revision=base_revision,
        )

    async def create_branch(
        self,
        repository_id: str,
        name: str,
        context: RepositoryCallContext,
        *,
        start_revision: str = "HEAD",
        checkout: bool = False,
    ) -> RepositoryRevision:
        binding = self._registry.resolve(repository_id)
        await self._enforce(
            binding.reference,
            RepositoryOperation.CREATE_BRANCH,
            context,
            payload={"name": name, "start_revision": start_revision, "checkout": checkout},
        )
        return await binding.provider.create_branch(
            binding.reference,
            name,
            context.operation,
            start_revision=start_revision,
            checkout=checkout,
        )

    async def checkout(
        self,
        repository_id: str,
        revision: str,
        context: RepositoryCallContext,
    ) -> RepositoryRevision:
        binding = self._registry.resolve(repository_id)
        await self._enforce(
            binding.reference,
            RepositoryOperation.CHECKOUT,
            context,
            payload={"revision": revision},
        )
        return await binding.provider.checkout(binding.reference, revision, context.operation)

    async def commit(
        self,
        repository_id: str,
        message: str,
        context: RepositoryCallContext,
        *,
        author_name: str,
        author_email: str,
    ) -> RepositoryCommit:
        binding = self._registry.resolve(repository_id)
        await self._enforce(
            binding.reference,
            RepositoryOperation.COMMIT,
            context,
            payload={"message": message, "author": author_name},
        )
        return await binding.provider.commit(
            binding.reference,
            message,
            context.operation,
            author_name=author_name,
            author_email=author_email,
        )

    async def fetch(
        self,
        repository_id: str,
        context: RepositoryCallContext,
    ) -> RepositoryRevision | None:
        binding = self._registry.resolve(repository_id)
        await self._enforce(binding.reference, RepositoryOperation.FETCH, context)
        return await binding.provider.fetch(binding.reference, context.operation)

    async def push(
        self,
        repository_id: str,
        context: RepositoryCallContext,
        *,
        remote: str = "origin",
        refspec: str | None = None,
    ) -> RepositoryRevision:
        binding = self._registry.resolve(repository_id)
        await self._enforce(
            binding.reference,
            RepositoryOperation.PUSH,
            context,
            payload={"remote": remote, "refspec": refspec},
        )
        return await binding.provider.push(
            binding.reference,
            context.operation,
            remote=remote,
            refspec=refspec,
        )

    async def _enforce(
        self,
        repository: RepositoryReference,
        operation: RepositoryOperation,
        context: RepositoryCallContext,
        *,
        payload: dict[str, object] | None = None,
    ) -> None:
        if operation in {
            RepositoryOperation.CREATE_BRANCH,
            RepositoryOperation.CHECKOUT,
            RepositoryOperation.COMMIT,
        }:
            action = AuthorizationAction.MODIFY
            side_effect = "local_write"
            risk = RiskClassification.ELEVATED
        elif operation is RepositoryOperation.PUSH:
            action = AuthorizationAction.MODIFY
            side_effect = "external"
            risk = RiskClassification.HIGH
        elif operation is RepositoryOperation.FETCH:
            action = AuthorizationAction.READ
            side_effect = "external_read_local_write"
            risk = RiskClassification.STANDARD
        else:
            action = AuthorizationAction.READ
            side_effect = None
            risk = RiskClassification.STANDARD
        actor = infer_actor_identity(context.actor_ref)
        proposed = ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=action,
                resource_type=ResourceType.GENERIC,
                resource_id=repository.id,
                operation=context.operation,
                task_id=context.task_id,
                run_id=context.run_id,
                agent_id=context.agent_id,
                capability_ref=operation.value,
                side_effect=side_effect,
                security_labels=("repository", operation.value),
            ),
            payload=payload,
        )
        await self._authorization.enforce(
            proposed,
            approval_id=context.approval_id,
            risk=risk,
        )


class RepositoryProvenanceStore:
    """Small platform-owned provenance seam for exact Run repository inputs/outputs."""

    def __init__(self) -> None:
        self._records: dict[str, list[RepositoryRunProvenance]] = {}

    def record(self, provenance: RepositoryRunProvenance) -> None:
        records = self._records.setdefault(provenance.run_id, [])
        key = (provenance.repository_id, provenance.input_revision, provenance.output_revision)
        if any(
            (item.repository_id, item.input_revision, item.output_revision) == key
            for item in records
        ):
            return
        records.append(provenance)

    def for_run(self, run_id: str) -> tuple[RepositoryRunProvenance, ...]:
        return tuple(self._records.get(run_id, ()))
