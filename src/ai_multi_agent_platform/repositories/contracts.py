"""Replaceable provider-neutral repository operations."""

from __future__ import annotations

from abc import abstractmethod

from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import OperationContext, ProviderDescriptor

from .models import (
    RepositoryCommit,
    RepositoryConnection,
    RepositoryDiff,
    RepositoryReference,
    RepositoryRevision,
    RepositoryStatus,
    RepositoryTree,
)


class RepositoryProvider(ProviderContract):
    """Canonical repository seam implemented by local Git or connector-backed providers."""

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @property
    def descriptor(self) -> ProviderDescriptor:
        """Expose repository adapters through the platform-wide provider metadata seam."""

        return ProviderDescriptor(
            provider_id=self.provider_id,
            provider_type="repository",
        )

    @abstractmethod
    async def discover(
        self,
        connection: RepositoryConnection,
        context: OperationContext,
    ) -> tuple[RepositoryReference, ...]: ...

    @abstractmethod
    async def read(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryReference: ...

    @abstractmethod
    async def resolve_revision(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryRevision: ...

    @abstractmethod
    async def read_tree(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree: ...

    @abstractmethod
    async def branches(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> tuple[str, ...]: ...

    @abstractmethod
    async def tags(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> tuple[str, ...]: ...

    @abstractmethod
    async def status(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryStatus: ...

    @abstractmethod
    async def diff(
        self,
        repository: RepositoryReference,
        context: OperationContext,
        *,
        base_revision: str | None = None,
    ) -> RepositoryDiff: ...

    @abstractmethod
    async def create_branch(
        self,
        repository: RepositoryReference,
        name: str,
        context: OperationContext,
        *,
        start_revision: str = "HEAD",
        checkout: bool = False,
    ) -> RepositoryRevision: ...

    @abstractmethod
    async def checkout(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryRevision: ...

    @abstractmethod
    async def commit(
        self,
        repository: RepositoryReference,
        message: str,
        context: OperationContext,
        *,
        author_name: str,
        author_email: str,
    ) -> RepositoryCommit: ...

    @abstractmethod
    async def fetch(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryRevision | None: ...

    @abstractmethod
    async def push(
        self,
        repository: RepositoryReference,
        context: OperationContext,
        *,
        remote: str = "origin",
        refspec: str | None = None,
    ) -> RepositoryRevision: ...
