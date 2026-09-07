"""Production wiring for repository intelligence through canonical repository policy."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.repositories import (
    RepositoryCallContext,
    RepositoryRegistry,
    RepositoryService,
    RepositoryTree,
)

RepositoryIntelligenceActorResolver = Callable[[OperationContext], str]


class AuthorizedRepositorySnapshotLoader:
    """Load an exact repository tree only after the canonical #82/#15 read gate succeeds.

    ``RepositoryService.read`` is the policy preflight. The loader then uses the already-bound
    provider only for the exact immutable tree read that ``RepositoryService`` does not yet expose
    as a public operation. This keeps provider selection in #82 and prevents repository-intelligence
    code from discovering private paths or constructing its own Git provider.
    """

    def __init__(
        self,
        repositories: RepositoryService,
        registry: RepositoryRegistry,
        *,
        actor_resolver: RepositoryIntelligenceActorResolver,
    ) -> None:
        self._repositories = repositories
        self._registry = registry
        self._actor_resolver = actor_resolver

    async def __call__(
        self,
        repository_id: str,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree:
        call_context = RepositoryCallContext(
            operation=context,
            actor_ref=self._actor_resolver(context),
        )
        authorized_reference = await self._repositories.read(repository_id, call_context)
        binding = self._registry.resolve(repository_id)
        if binding.reference.id != authorized_reference.id:
            raise RuntimeError("authorized repository binding changed during snapshot resolution")
        return await binding.provider.read_tree(binding.reference, revision, context)
