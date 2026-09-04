"""RepositoryProvider bridge over the provider-neutral #44 ConnectorProvider contract."""

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


class ConnectorRepositoryProvider(RepositoryProvider):
    """Translate generic repository operations into connector resource/action contracts.

    Hosted Git services and self-hosted forges implement the #44 ConnectorProvider seam.
    This bridge owns no GitHub/GitLab/Gitea types and never receives credential values;
    authentication remains attached to the canonical Connection as SecretReferences.
    """

    def __init__(
        self,
        connector: ConnectorProvider,
        connection: RepositoryConnection,
        *,
        provider_id: str | None = None,
    ) -> None:
        if connection.local:
            raise ValueError("ConnectorRepositoryProvider requires a non-local connection")
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
        return tuple(self._wrap_resource(resource) for resource in resources)

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
        return self._wrap_resource(resource, previous=repository)

    async def resolve_revision(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryRevision:
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.READ,
            "repository.resolve_revision",
            {"revision": _nonblank(revision, "revision")},
            context,
        )
        commit_sha = _string(output, "commit_sha", "repository.resolve_revision")
        return RepositoryRevision(repository.id, revision, commit_sha)

    async def read_tree(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree:
        result = await self._invoke(
            repository,
            RepositoryOperation.MATERIALIZE,
            "repository.read_tree",
            {"revision": _nonblank(revision, "revision")},
            context,
        )
        output = _mapping(result.output, "repository.read_tree")
        resolved_revision = _string(output, "resolved_revision", "repository.read_tree")
        validate_git_revision(resolved_revision)
        resources = {resource.id: resource for resource in result.resource_refs}
        raw_entries = output.get("entries")
        if not isinstance(raw_entries, list):
            raise _invalid_response("repository.read_tree", "entries must be an array")
        entries: list[RepositoryTreeEntry] = []
        for index, raw_entry in enumerate(raw_entries):
            if not isinstance(raw_entry, dict):
                raise _invalid_response(
                    "repository.read_tree",
                    f"entries[{index}] must be an object",
                )
            path = _string(raw_entry, "relative_path", "repository.read_tree")
            validate_relative_path(path)
            resource_id = _string(raw_entry, "resource_id", "repository.read_tree")
            resource = resources.get(resource_id)
            if resource is None:
                raise _invalid_response(
                    "repository.read_tree",
                    f"missing file resource reference: {resource_id}",
                )
            if resource.connection_id != self._connection.id:
                raise _invalid_response(
                    "repository.read_tree",
                    "file resource belongs to another connection",
                )
            data = await self._connector.import_file_content(
                self._connection.connection,
                resource,
                context,
            )
            entries.append(RepositoryTreeEntry(path, data))
        return RepositoryTree(
            repository_id=repository.id,
            requested_ref=revision,
            resolved_revision=resolved_revision,
            entries=tuple(entries),
        )

    async def branches(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> tuple[str, ...]:
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.INSPECT_REFS,
            "repository.branches",
            {},
            context,
        )
        return _string_tuple(output.get("branches"), "repository.branches", "branches")

    async def tags(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> tuple[str, ...]:
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.INSPECT_REFS,
            "repository.tags",
            {},
            context,
        )
        return _string_tuple(output.get("tags"), "repository.tags", "tags")

    async def status(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryStatus:
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.STATUS,
            "repository.status",
            {},
            context,
        )
        return RepositoryStatus(
            repository_id=repository.id,
            head_revision=_optional_revision(output.get("head_revision"), "repository.status"),
            branch=_optional_string(output.get("branch"), "repository.status", "branch"),
            staged_paths=_path_tuple(output.get("staged_paths"), "repository.status", "staged_paths"),
            modified_paths=_path_tuple(
                output.get("modified_paths"), "repository.status", "modified_paths"
            ),
            deleted_paths=_path_tuple(output.get("deleted_paths"), "repository.status", "deleted_paths"),
            untracked_paths=_path_tuple(
                output.get("untracked_paths"), "repository.status", "untracked_paths"
            ),
        )

    async def diff(
        self,
        repository: RepositoryReference,
        context: OperationContext,
        *,
        base_revision: str | None = None,
    ) -> RepositoryDiff:
        arguments: dict[str, JsonValue] = {}
        if base_revision is not None:
            arguments["base_revision"] = _nonblank(base_revision, "base_revision")
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.DIFF,
            "repository.diff",
            arguments,
            context,
        )
        base = _optional_revision(output.get("base_revision"), "repository.diff")
        patch = _string(output, "patch", "repository.diff", allow_empty=True)
        return RepositoryDiff(
            repository.id,
            base,
            patch,
            _path_tuple(output.get("changed_paths"), "repository.diff", "changed_paths"),
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
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.CREATE_BRANCH,
            "repository.create_branch",
            {
                "name": _nonblank(name, "branch name"),
                "start_revision": _nonblank(start_revision, "start_revision"),
                "checkout": checkout,
            },
            context,
        )
        commit_sha = _string(output, "commit_sha", "repository.create_branch")
        return RepositoryRevision(repository.id, name, commit_sha)

    async def checkout(
        self,
        repository: RepositoryReference,
        revision: str,
        context: OperationContext,
    ) -> RepositoryRevision:
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.CHECKOUT,
            "repository.checkout",
            {"revision": _nonblank(revision, "revision")},
            context,
        )
        commit_sha = _string(output, "commit_sha", "repository.checkout")
        return RepositoryRevision(repository.id, revision, commit_sha)

    async def commit(
        self,
        repository: RepositoryReference,
        message: str,
        context: OperationContext,
        *,
        author_name: str,
        author_email: str,
    ) -> RepositoryCommit:
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.COMMIT,
            "repository.commit",
            {
                "message": _nonblank(message, "commit message"),
                "author_name": _nonblank(author_name, "author_name"),
                "author_email": _nonblank(author_email, "author_email"),
            },
            context,
        )
        return RepositoryCommit(
            repository_id=repository.id,
            revision=_string(output, "revision", "repository.commit"),
            message=message,
            parent_revisions=_revision_tuple(
                output.get("parent_revisions"),
                "repository.commit",
                "parent_revisions",
            ),
        )

    async def fetch(
        self,
        repository: RepositoryReference,
        context: OperationContext,
    ) -> RepositoryRevision | None:
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.FETCH,
            "repository.fetch",
            {},
            context,
        )
        revision = output.get("commit_sha")
        if revision is None:
            return None
        if not isinstance(revision, str):
            raise _invalid_response("repository.fetch", "commit_sha must be a string or null")
        return RepositoryRevision(repository.id, "HEAD", revision)

    async def push(
        self,
        repository: RepositoryReference,
        context: OperationContext,
        *,
        remote: str = "origin",
        refspec: str | None = None,
    ) -> RepositoryRevision:
        arguments: dict[str, JsonValue] = {"remote": _nonblank(remote, "remote")}
        if refspec is not None:
            arguments["refspec"] = _nonblank(refspec, "refspec")
        output = await self._invoke_mapping(
            repository,
            RepositoryOperation.PUSH,
            "repository.push",
            arguments,
            context,
        )
        return RepositoryRevision(
            repository.id,
            refspec or "HEAD",
            _string(output, "commit_sha", "repository.push"),
        )

    async def _invoke_mapping(
        self,
        repository: RepositoryReference,
        operation: RepositoryOperation,
        action: str,
        arguments: Mapping[str, JsonValue],
        context: OperationContext,
    ) -> Mapping[str, JsonValue]:
        result = await self._invoke(repository, operation, action, arguments, context)
        return _mapping(result.output, action)

    async def _invoke(
        self,
        repository: RepositoryReference,
        operation: RepositoryOperation,
        action: str,
        arguments: Mapping[str, JsonValue],
        context: OperationContext,
    ) -> ConnectorActionResult:
        self._require_repository(repository)
        invocation_arguments: dict[str, JsonValue] = {
            "repository": repository.external_resource.to_dict(),
            **dict(arguments),
        }
        result = await self._connector.invoke_action(
            ConnectorActionInvocation(
                invocation_id=f"repository-{uuid4()}",
                connection_id=self._connection.id,
                action=action,
                arguments=invocation_arguments,
                context=context,
            )
        )
        if result.invocation_id == "":
            raise _invalid_response(action, "connector returned a blank invocation_id")
        self._require_operation(repository, operation)
        return result

    def _wrap_resource(
        self,
        resource: ExternalResourceReference,
        *,
        previous: RepositoryReference | None = None,
    ) -> RepositoryReference:
        if resource.connection_id != self._connection.id:
            raise _invalid_response("repository resource", "resource belongs to another connection")
        if resource.resource_type != "repository":
            raise _invalid_response("repository resource", "resource_type must be 'repository'")
        default_branch = _metadata_string(resource.metadata, "default_branch")
        visibility_value = _metadata_string(resource.metadata, "visibility")
        try:
            visibility = (
                RepositoryVisibility(visibility_value)
                if visibility_value is not None
                else RepositoryVisibility.UNKNOWN
            )
        except ValueError:
            visibility = RepositoryVisibility.UNKNOWN
        resolved_revision = _validated_revision_or_none(resource.revision)
        capabilities = previous.capabilities if previous is not None else self._capabilities()
        return RepositoryReference(
            external_resource=resource,
            default_branch=default_branch or (previous.default_branch if previous else None),
            target_revision=previous.target_revision if previous else None,
            resolved_revision=resolved_revision,
            visibility=visibility,
            capabilities=capabilities,
            metadata={"provider": self.provider_id},
        )

    def _capabilities(self) -> tuple:
        actions = set(self._connector.definition.actions)
        operations: list[RepositoryOperation] = [
            RepositoryOperation.DISCOVER,
            RepositoryOperation.READ,
        ]
        action_operations = {
            "repository.read_tree": RepositoryOperation.MATERIALIZE,
            "repository.fetch": RepositoryOperation.FETCH,
            "repository.branches": RepositoryOperation.INSPECT_REFS,
            "repository.tags": RepositoryOperation.INSPECT_REFS,
            "repository.create_branch": RepositoryOperation.CREATE_BRANCH,
            "repository.checkout": RepositoryOperation.CHECKOUT,
            "repository.status": RepositoryOperation.STATUS,
            "repository.diff": RepositoryOperation.DIFF,
            "repository.commit": RepositoryOperation.COMMIT,
            "repository.push": RepositoryOperation.PUSH,
        }
        for action, operation in action_operations.items():
            if action in actions and operation not in operations:
                operations.append(operation)
        return tuple(
            repository_capability(
                operation,
                requires_credentials=operation in CREDENTIAL_OPERATIONS,
            )
            for operation in operations
        )

    def _require_connection(self, connection: RepositoryConnection) -> None:
        if connection.id != self._connection.id:
            raise ContractError(ErrorCode.NOT_FOUND, "repository connection is not bound to provider")

    def _require_repository(self, repository: RepositoryReference) -> None:
        if repository.connection_id != self._connection.id:
            raise ContractError(ErrorCode.NOT_FOUND, "repository is not bound to connector provider")

    @staticmethod
    def _require_operation(
        repository: RepositoryReference,
        operation: RepositoryOperation,
    ) -> None:
        supported = {
            capability.operation
            for capability in repository.capabilities
            if capability.supported
        }
        if supported and operation not in supported:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"repository operation is not advertised: {operation.value}",
            )


def _mapping(value: JsonValue, action: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise _invalid_response(action, "output must be an object")
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
        raise _invalid_response(action, f"{key} must be a string")
    return raw


def _optional_string(value: JsonValue | None, action: str, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid_response(action, f"{field} must be a string or null")
    return value


def _optional_revision(value: JsonValue | None, action: str) -> str | None:
    revision = _optional_string(value, action, "revision")
    return validate_git_revision(revision) if revision is not None else None


def _string_tuple(value: JsonValue | None, action: str, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise _invalid_response(action, f"{field} must be an array of strings")
    return tuple(value)


def _path_tuple(value: JsonValue | None, action: str, field: str) -> tuple[str, ...]:
    values = _string_tuple(value, action, field)
    for path in values:
        validate_relative_path(path)
    return values


def _revision_tuple(value: JsonValue | None, action: str, field: str) -> tuple[str, ...]:
    values = _string_tuple(value, action, field)
    return tuple(validate_git_revision(revision) for revision in values)


def _metadata_string(metadata: Mapping[str, JsonValue], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _validated_revision_or_none(value: str | None) -> str | None:
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


def _invalid_response(action: str, message: str) -> ContractError:
    return ContractError(
        ErrorCode.INVALID_PROVIDER_RESPONSE,
        f"{action}: {message}",
    )
