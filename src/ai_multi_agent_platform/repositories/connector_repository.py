"""Provider-neutral repository bridge over the #44 connector contract."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from ai_multi_agent_platform.connectors import (
    ConnectorActionInvocation,
    ConnectorActionResult,
    ConnectorProvider,
    ConnectorResourceQuery,
    ExternalResourceReference,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.workspaces.models import validate_relative_path

from .capabilities import CREDENTIAL_OPERATIONS, repository_capability
from .contracts import RepositoryProvider
from .models import (
    RepositoryCapability,
    RepositoryCommit,
    RepositoryConnection,
    RepositoryDiff,
    RepositoryOperation,
    RepositoryReference,
    RepositoryRevision,
    RepositoryStatus,
    RepositoryTree,
    RepositoryTreeEntry,
    RepositoryVisibility,
    validate_git_revision,
)

_ACTIONS: dict[RepositoryOperation, str] = {
    RepositoryOperation.MATERIALIZE: "repository.read_tree",
    RepositoryOperation.FETCH: "repository.fetch",
    RepositoryOperation.CREATE_BRANCH: "repository.create_branch",
    RepositoryOperation.CHECKOUT: "repository.checkout",
    RepositoryOperation.STATUS: "repository.status",
    RepositoryOperation.DIFF: "repository.diff",
    RepositoryOperation.COMMIT: "repository.commit",
    RepositoryOperation.PUSH: "repository.push",
}
_REF_ACTIONS = {"repository.branches", "repository.tags"}


class ConnectorRepositoryProvider(RepositoryProvider):
    """Expose hosted/self-hosted repositories through a replaceable ConnectorProvider."""

    def __init__(
        self,
        connector: ConnectorProvider,
        connection: RepositoryConnection,
        *,
        provider_id: str | None = None,
    ) -> None:
        if connection.local:
            raise ValueError("connector repository provider requires a non-local connection")
        self._connector = connector
        self._connection = connection
        self._provider_id = provider_id or f"repository-{connector.descriptor.provider_id}"
        if not self._provider_id.strip():
            raise ValueError("repository connector provider_id must not be blank")
        if connection.provider_id != self._provider_id:
            raise ValueError("repository connection provider_id must match connector bridge")

    @property
    def provider_id(self) -> str:
        return self._provider_id

    async def discover(
        self,
        connection: RepositoryConnection,
        context: OperationContext,
    ) -> tuple[RepositoryReference, ...]:
        self._require_connection(connection)
        resources = await self._connector.list_resources(
            ConnectorResourceQuery(
                connection_id=connection.id,
                resource_type="repository",
                context=context,
                query=connection.metadata,
            )
        )
        return tuple(self._wrap(resource) for resource in resources)

    async def read(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryReference:
        self._require_repository(repository)
        resource = await self._connector.read_resource(
            self._connection.connection,
            repository.external_resource,
            context,
        )
        return self._wrap(resource, previous=repository)

    async def resolve_revision(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryRevision:
        output = await self._mapping_action(
            repository,
            RepositoryOperation.READ,
            "repository.resolve_revision",
            {"revision": _nonblank(revision, "revision")},
            context,
        )
        return RepositoryRevision(
            repository.id,
            revision,
            _revision(output.get("commit_sha"), "repository.resolve_revision", "commit_sha"),
        )

    async def read_tree(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree:
        action = "repository.read_tree"
        result = await self._action(
            repository,
            RepositoryOperation.MATERIALIZE,
            action,
            {"revision": _nonblank(revision, "revision")},
            context,
        )
        output = _mapping(result.output, action)
        resolved = _revision(output.get("resolved_revision"), action, "resolved_revision")
        resource_map = {resource.id: resource for resource in result.resource_refs}
        raw_entries = output.get("entries")
        if not isinstance(raw_entries, list):
            raise _invalid(action, "entries must be an array")
        entries: list[RepositoryTreeEntry] = []
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, dict):
                raise _invalid(action, f"entries[{index}] must be an object")
            path = _path(_string(raw, "relative_path", action), action)
            resource_id = _string(raw, "resource_id", action)
            resource = resource_map.get(resource_id)
            if resource is None:
                raise _invalid(action, f"missing file resource: {resource_id}")
            if resource.connection_id != self._connection.id:
                raise _invalid(action, "file resource belongs to another connection")
            data = await self._connector.import_file_content(
                self._connection.connection,
                resource,
                context,
            )
            entries.append(RepositoryTreeEntry(path, data))
        return RepositoryTree(repository.id, revision, resolved, tuple(entries))

    async def branches(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> tuple[str, ...]:
        output = await self._mapping_action(
            repository,
            RepositoryOperation.INSPECT_REFS,
            "repository.branches",
            {},
            context,
        )
        return _strings(output.get("branches"), "repository.branches", "branches")

    async def tags(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> tuple[str, ...]:
        output = await self._mapping_action(
            repository,
            RepositoryOperation.INSPECT_REFS,
            "repository.tags",
            {},
            context,
        )
        return _strings(output.get("tags"), "repository.tags", "tags")

    async def status(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryStatus:
        action = "repository.status"
        output = await self._mapping_action(
            repository,
            RepositoryOperation.STATUS,
            action,
            {},
            context,
        )
        return RepositoryStatus(
            repository_id=repository.id,
            head_revision=_optional_revision(output.get("head_revision"), action),
            branch=_optional_string(output.get("branch"), action, "branch"),
            staged_paths=_paths(output.get("staged_paths"), action, "staged_paths"),
            modified_paths=_paths(output.get("modified_paths"), action, "modified_paths"),
            deleted_paths=_paths(output.get("deleted_paths"), action, "deleted_paths"),
            untracked_paths=_paths(output.get("untracked_paths"), action, "untracked_paths"),
        )

    async def diff(
        self,
        repository: RepositoryReference,
        context: OperationContext,
        *,
        base_revision: str | None = None,
    ) -> RepositoryDiff:
        action = "repository.diff"
        arguments: dict[str, JsonValue] = {}
        if base_revision is not None:
            arguments["base_revision"] = _nonblank(base_revision, "base_revision")
        output = await self._mapping_action(
            repository,
            RepositoryOperation.DIFF,
            action,
            arguments,
            context,
        )
        return RepositoryDiff(
            repository_id=repository.id,
            base_revision=_optional_revision(output.get("base_revision"), action),
            patch=_string(output, "patch", action, allow_empty=True),
            changed_paths=_paths(output.get("changed_paths"), action, "changed_paths"),
        )

    async def create_branch(
        self,
        repository: RepositoryReference,
        name: str,
        context: OperationContext,
        *,
        start_revision: str = "HEAD",
        checkout: bool = False,
    ) -> RepositoryRevision:
        action = "repository.create_branch"
        output = await self._mapping_action(
            repository,
            RepositoryOperation.CREATE_BRANCH,
            action,
            {
                "name": _nonblank(name, "branch name"),
                "start_revision": _nonblank(start_revision, "start_revision"),
                "checkout": checkout,
            },
            context,
        )
        return RepositoryRevision(
            repository.id,
            name,
            _revision(output.get("commit_sha"), action, "commit_sha"),
        )

    async def checkout(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryRevision:
        action = "repository.checkout"
        output = await self._mapping_action(
            repository,
            RepositoryOperation.CHECKOUT,
            action,
            {"revision": _nonblank(revision, "revision")},
            context,
        )
        return RepositoryRevision(
            repository.id,
            revision,
            _revision(output.get("commit_sha"), action, "commit_sha"),
        )

    async def commit(
        self,
        repository: RepositoryReference,
        message: str,
        context: OperationContext,
        *,
        author_name: str,
        author_email: str,
    ) -> RepositoryCommit:
        action = "repository.commit"
        output = await self._mapping_action(
            repository,
            RepositoryOperation.COMMIT,
            action,
            {
                "message": _nonblank(message, "commit message"),
                "author_name": _nonblank(author_name, "author_name"),
                "author_email": _nonblank(author_email, "author_email"),
            },
            context,
        )
        return RepositoryCommit(
            repository_id=repository.id,
            revision=_revision(output.get("revision"), action, "revision"),
            message=message,
            parent_revisions=_revisions(
                output.get("parent_revisions"),
                action,
                "parent_revisions",
            ),
        )

    async def fetch(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryRevision | None:
        action = "repository.fetch"
        output = await self._mapping_action(
            repository,
            RepositoryOperation.FETCH,
            action,
            {},
            context,
        )
        value = output.get("commit_sha")
        if value is None:
            return None
        return RepositoryRevision(repository.id, "HEAD", _revision(value, action, "commit_sha"))

    async def push(
        self,
        repository: RepositoryReference,
        context: OperationContext,
        *,
        remote: str = "origin",
        refspec: str | None = None,
    ) -> RepositoryRevision:
        action = "repository.push"
        arguments: dict[str, JsonValue] = {"remote": _nonblank(remote, "remote")}
        if refspec is not None:
            arguments["refspec"] = _nonblank(refspec, "refspec")
        output = await self._mapping_action(
            repository,
            RepositoryOperation.PUSH,
            action,
            arguments,
            context,
        )
        return RepositoryRevision(
            repository.id,
            refspec or "HEAD",
            _revision(output.get("commit_sha"), action, "commit_sha"),
        )

    async def _mapping_action(
        self,
        repository: RepositoryReference,
        operation: RepositoryOperation,
        action: str,
        arguments: Mapping[str, JsonValue],
        context: OperationContext,
    ) -> Mapping[str, JsonValue]:
        result = await self._action(repository, operation, action, arguments, context)
        return _mapping(result.output, action)

    async def _action(
        self,
        repository: RepositoryReference,
        operation: RepositoryOperation,
        action: str,
        arguments: Mapping[str, JsonValue],
        context: OperationContext,
    ) -> ConnectorActionResult:
        self._require_repository(repository)
        self._require_operation(repository, operation)
        invocation_arguments: dict[str, JsonValue] = {
            "repository": repository.external_resource.to_dict(),
            **dict(arguments),
        }
        return await self._connector.invoke_action(
            ConnectorActionInvocation(
                invocation_id=f"repository-{uuid4()}",
                connection_id=self._connection.id,
                action=action,
                arguments=invocation_arguments,
                context=context,
            )
        )

    def _wrap(
        self,
        resource: ExternalResourceReference,
        *,
        previous: RepositoryReference | None = None,
    ) -> RepositoryReference:
        if resource.connection_id != self._connection.id:
            raise _invalid("repository resource", "resource belongs to another connection")
        if resource.resource_type != "repository":
            raise _invalid("repository resource", "resource_type must be 'repository'")
        visibility_value = _metadata_string(resource.metadata, "visibility")
        try:
            visibility = (
                RepositoryVisibility(visibility_value)
                if visibility_value is not None
                else RepositoryVisibility.UNKNOWN
            )
        except ValueError:
            visibility = RepositoryVisibility.UNKNOWN
        return RepositoryReference(
            external_resource=resource,
            default_branch=(
                _metadata_string(resource.metadata, "default_branch")
                or (previous.default_branch if previous else None)
            ),
            target_revision=previous.target_revision if previous else None,
            resolved_revision=_revision_or_none(resource.revision),
            visibility=visibility,
            capabilities=previous.capabilities if previous else self._capabilities(),
            metadata={"provider": self.provider_id},
        )

    def _capabilities(self) -> tuple[RepositoryCapability, ...]:
        actions = set(self._connector.definition.actions)
        operations: list[RepositoryOperation] = [
            RepositoryOperation.DISCOVER,
            RepositoryOperation.READ,
        ]
        for operation, action in _ACTIONS.items():
            if action in actions:
                operations.append(operation)
        if actions.intersection(_REF_ACTIONS):
            operations.append(RepositoryOperation.INSPECT_REFS)
        return tuple(
            repository_capability(
                operation,
                requires_credentials=operation in CREDENTIAL_OPERATIONS,
            )
            for operation in dict.fromkeys(operations)
        )

    def _require_connection(self, connection: RepositoryConnection) -> None:
        if connection.id != self._connection.id:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "repository connection is not bound to provider",
            )

    def _require_repository(self, repository: RepositoryReference) -> None:
        if repository.connection_id != self._connection.id:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "repository is not bound to connector provider",
            )

    @staticmethod
    def _require_operation(
        repository: RepositoryReference,
        operation: RepositoryOperation,
    ) -> None:
        supported = {
            capability.operation for capability in repository.capabilities if capability.supported
        }
        if supported and operation not in supported:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"repository operation is not advertised: {operation.value}",
            )


def _mapping(value: JsonValue, action: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise _invalid(action, "output must be an object")
    return value


def _string(
    value: Mapping[str, JsonValue],
    key: str,
    action: str,
    *,
    allow_empty: bool = False,
) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or (not allow_empty and not raw.strip()):
        raise _invalid(action, f"{key} must be a string")
    return raw


def _optional_string(value: JsonValue | None, action: str, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(action, f"{field} must be a string or null")
    return value


def _revision(value: JsonValue | None, action: str, field: str) -> str:
    if not isinstance(value, str):
        raise _invalid(action, f"{field} must be an immutable Git revision")
    try:
        return validate_git_revision(value)
    except ValueError as exc:
        raise _invalid(action, f"{field} must be an immutable Git revision") from exc


def _optional_revision(value: JsonValue | None, action: str) -> str | None:
    return None if value is None else _revision(value, action, "revision")


def _strings(value: JsonValue | None, action: str, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _invalid(action, f"{field} must be an array of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _invalid(action, f"{field} must be an array of strings")
        result.append(item)
    return tuple(result)


def _path(value: str, action: str) -> str:
    try:
        return validate_relative_path(value)
    except ValueError as exc:
        raise _invalid(action, "connector returned an invalid repository path") from exc


def _paths(value: JsonValue | None, action: str, field: str) -> tuple[str, ...]:
    return tuple(_path(item, action) for item in _strings(value, action, field))


def _revisions(value: JsonValue | None, action: str, field: str) -> tuple[str, ...]:
    return tuple(_revision(item, action, field) for item in _strings(value, action, field))


def _metadata_string(metadata: Mapping[str, JsonValue], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _revision_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return validate_git_revision(value)
    except ValueError:
        return None


def _nonblank(value: str, field: str) -> str:
    if not value.strip():
        raise ValueError(f"{field} must not be blank")
    return value


def _invalid(action: str, message: str) -> ContractError:
    return ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, f"{action}: {message}")
