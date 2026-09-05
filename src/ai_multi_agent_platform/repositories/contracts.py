"""Replaceable provider-neutral repository operations."""

from __future__ import annotations

from abc import abstractmethod

from ai_multi_agent_platform.connectors import ExternalResourceReference
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import OperationContext, ProviderDescriptor

from .models import (
    RepositoryChangeRequest,
    RepositoryChangeRequestState,
    RepositoryCommit,
    RepositoryCommitInfo,
    RepositoryConnection,
    RepositoryDiff,
    RepositoryIssue,
    RepositoryIssueState,
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

    async def commits(
        self,
        repository: RepositoryReference,
        context: OperationContext,
        *,
        revision: str = "HEAD",
        limit: int = 50,
    ) -> tuple[RepositoryCommitInfo, ...]:
        """Inspect commit history when the concrete repository provider supports it."""

        del repository, context, revision, limit
        raise self._unsupported("commit history inspection")

    async def read_issue(
        self,
        repository: RepositoryReference,
        issue: ExternalResourceReference,
        context: OperationContext,
    ) -> RepositoryIssue:
        del repository, issue, context
        raise self._unsupported("issue reads")

    async def open_issue(
        self,
        repository: RepositoryReference,
        title: str,
        context: OperationContext,
        *,
        body: str | None = None,
    ) -> RepositoryIssue:
        del repository, title, context, body
        raise self._unsupported("issue creation")

    async def update_issue(
        self,
        repository: RepositoryReference,
        issue: ExternalResourceReference,
        context: OperationContext,
        *,
        title: str | None = None,
        body: str | None = None,
        state: RepositoryIssueState | None = None,
    ) -> RepositoryIssue:
        del repository, issue, context, title, body, state
        raise self._unsupported("issue updates")

    async def read_change_request(
        self,
        repository: RepositoryReference,
        change_request: ExternalResourceReference,
        context: OperationContext,
    ) -> RepositoryChangeRequest:
        del repository, change_request, context
        raise self._unsupported("change-request reads")

    async def open_change_request(
        self,
        repository: RepositoryReference,
        title: str,
        head_ref: str,
        base_ref: str,
        context: OperationContext,
        *,
        body: str | None = None,
    ) -> RepositoryChangeRequest:
        del repository, title, head_ref, base_ref, context, body
        raise self._unsupported("change-request creation")

    async def update_change_request(
        self,
        repository: RepositoryReference,
        change_request: ExternalResourceReference,
        context: OperationContext,
        *,
        title: str | None = None,
        body: str | None = None,
        state: RepositoryChangeRequestState | None = None,
    ) -> RepositoryChangeRequest:
        del repository, change_request, context, title, body, state
        raise self._unsupported("change-request updates")

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

    def _unsupported(self, operation: str) -> ContractError:
        return ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"repository provider does not support {operation}",
            provider_id=self.provider_id,
        )
