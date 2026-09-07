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

_DEFAULT_MAX_TREE_ENTRIES = 5_000
_DEFAULT_MAX_TREE_BYTES = 64 * 1024 * 1024


class AuthorizedRepositorySnapshotLoader:
    """Load exact trees through #82/#15 with pre-materialization resource ceilings."""

    def __init__(
        self,
        repositories: RepositoryService,
        *,
        actor_resolver: RepositoryIntelligenceActorResolver,
        max_entries: int = _DEFAULT_MAX_TREE_ENTRIES,
        max_total_bytes: int = _DEFAULT_MAX_TREE_BYTES,
    ) -> None:
        if max_entries < 1 or max_total_bytes < 1:
            raise ValueError("repository intelligence tree limits must be positive")
        self._repositories = repositories
        self._actor_resolver = actor_resolver
        self._max_entries = max_entries
        self._max_total_bytes = max_total_bytes

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
        return await self._repositories.read_tree(
            repository_id,
            revision,
            call_context,
            max_entries=self._max_entries,
            max_total_bytes=self._max_total_bytes,
        )
