"""Production wiring for repository intelligence through canonical repository policy."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.repositories import (
    RepositoryCallContext,
    RepositoryService,
    RepositoryTree,
)

RepositoryIntelligenceActorResolver = Callable[[OperationContext], str]


class AuthorizedRepositorySnapshotLoader:
    """Load exact repository trees only through the canonical #82/#15 read boundary."""

    def __init__(
        self,
        repositories: RepositoryService,
        *,
        actor_resolver: RepositoryIntelligenceActorResolver,
    ) -> None:
        self._repositories = repositories
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
        return await self._repositories.read_tree(repository_id, revision, call_context)
