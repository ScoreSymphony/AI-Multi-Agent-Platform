"""Bridge canonical repository operations into the #12 capability invocation path."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilitySpec,
    CapabilityToolProvider,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)

from .capabilities import RepositoryOperation, repository_capability_specs
from .models import RepositoryChangeRequestState, RepositoryIssueState
from .references import collaboration_reference_from_json
from .service import RepositoryCallContext, RepositoryService

RepositoryActorResolver = Callable[[OperationContext], str]

_TOOL_OPERATIONS = frozenset(
    {
        RepositoryOperation.READ,
        RepositoryOperation.INSPECT_REFS,
        RepositoryOperation.STATUS,
        RepositoryOperation.DIFF,
        RepositoryOperation.FETCH,
        RepositoryOperation.CREATE_BRANCH,
        RepositoryOperation.CHECKOUT,
        RepositoryOperation.COMMIT,
        RepositoryOperation.PUSH,
        RepositoryOperation.ISSUE_READ,
        RepositoryOperation.ISSUE_WRITE,
        RepositoryOperation.CHANGE_REQUEST_READ,
        RepositoryOperation.CHANGE_REQUEST_WRITE,
    }
)


class RepositoryCapabilityProvider(CapabilityToolProvider):
    """Route Agent/tool invocations through the policy-enforced RepositoryService."""

    def __init__(
        self,
        repositories: RepositoryService,
        *,
        actor_resolver: RepositoryActorResolver,
        provider_id: str = "platform.repository-bridge",
    ) -> None:
        if not provider_id.strip():
            raise ValueError("repository capability provider_id must not be blank")
        self._repositories = repositories
        self._actor_resolver = actor_resolver
        self._provider_id = provider_id

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="repository_bridge",
            supported_operations=("discover", "invoke"),
            capabilities=tuple(
                Capability(
                    name=operation.value,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                )
                for operation in sorted(_TOOL_OPERATIONS, key=lambda value: value.value)
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        operations = {operation.value for operation in _TOOL_OPERATIONS}
        specs = {
            spec.capability_id: spec
            for spec in repository_capability_specs()
            if spec.capability_id in operations
        }
        registrations: list[CapabilityRegistration] = []
        for operation in sorted(_TOOL_OPERATIONS, key=lambda value: value.value):
            spec = specs[operation.value]
            registrations.append(
                CapabilityRegistration(
                    capability=CapabilitySpec(
                        capability_id=spec.capability_id,
                        name=spec.name,
                        version=spec.version,
                        description=spec.description,
                        input_schema=_input_schema(operation),
                        output_schema=spec.output_schema,
                        tags=spec.tags,
                        safety=spec.safety,
                        side_effects=spec.side_effects,
                        required_permissions=spec.required_permissions,
                        required_approvals=spec.required_approvals,
                        required_worker_capabilities=spec.required_worker_capabilities,
                        timeout_seconds=spec.timeout_seconds,
                        health=HealthStatus.HEALTHY,
                        available=True,
                        features=spec.features,
                        credential_requirement=spec.credential_requirement,
                    ),
                    provider_id=self._provider_id,
                    provider_tool_ref=operation.value,
                    priority=100,
                )
            )
        return tuple(registrations)

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        try:
            operation = RepositoryOperation(invocation.tool_ref)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"repository capability bridge does not expose {invocation.tool_ref!r}",
                provider_id=self._provider_id,
            ) from exc
        if operation not in _TOOL_OPERATIONS:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"repository capability is not invokable through this bridge: {operation.value}",
                provider_id=self._provider_id,
            )

        arguments = invocation.arguments_json()
        repository_id = _required_string(arguments, "repository_id")
        context = RepositoryCallContext(
            operation=invocation.context,
            actor_ref=self._actor_resolver(invocation.context),
        )
        output = await self._invoke_operation(operation, repository_id, arguments, context)
        return ToolResult(invocation_id=invocation.invocation_id, output=output)

    async def _invoke_operation(
        self,
        operation: RepositoryOperation,
        repository_id: str,
        arguments: dict[str, JsonValue],
        context: RepositoryCallContext,
    ) -> dict[str, JsonValue]:
        if operation is RepositoryOperation.READ:
            return (await self._repositories.read(repository_id, context)).to_dict()
        if operation is RepositoryOperation.INSPECT_REFS:
            return await self._inspect_refs(repository_id, arguments, context)
        if operation is RepositoryOperation.ISSUE_READ:
            issue = collaboration_reference_from_json(
                arguments.get("resource"),
                expected_resource_type="repository_issue",
            )
            return (await self._repositories.read_issue(repository_id, issue, context)).to_dict()
        if operation is RepositoryOperation.ISSUE_WRITE:
            return await self._write_issue(repository_id, arguments, context)
        if operation is RepositoryOperation.CHANGE_REQUEST_READ:
            change_request = collaboration_reference_from_json(
                arguments.get("resource"),
                expected_resource_type="repository_change_request",
            )
            return (
                await self._repositories.read_change_request(
                    repository_id,
                    change_request,
                    context,
                )
            ).to_dict()
        if operation is RepositoryOperation.CHANGE_REQUEST_WRITE:
            return await self._write_change_request(repository_id, arguments, context)
        if operation is RepositoryOperation.STATUS:
            status = await self._repositories.status(repository_id, context)
            return {
                "repository_id": status.repository_id,
                "head_revision": status.head_revision,
                "branch": status.branch,
                "clean": status.clean,
                "staged_paths": list(status.staged_paths),
                "modified_paths": list(status.modified_paths),
                "deleted_paths": list(status.deleted_paths),
                "untracked_paths": list(status.untracked_paths),
            }
        if operation is RepositoryOperation.DIFF:
            diff = await self._repositories.diff(
                repository_id,
                context,
                base_revision=_optional_string(arguments, "base_revision"),
            )
            return {
                "repository_id": diff.repository_id,
                "base_revision": diff.base_revision,
                "changed_paths": list(diff.changed_paths),
                "patch": diff.patch,
            }
        if operation is RepositoryOperation.FETCH:
            revision = await self._repositories.fetch(repository_id, context)
            if revision is None:
                return {"repository_id": repository_id, "updated": False, "commit_sha": None}
            return {
                "repository_id": revision.repository_id,
                "requested_ref": revision.requested_ref,
                "commit_sha": revision.commit_sha,
                "updated": True,
            }
        if operation is RepositoryOperation.CREATE_BRANCH:
            revision = await self._repositories.create_branch(
                repository_id,
                _required_string(arguments, "name"),
                context,
                start_revision=_optional_string(arguments, "start_revision") or "HEAD",
                checkout=_optional_bool(arguments, "checkout") or False,
            )
            return _revision_output(
                revision.repository_id, revision.requested_ref, revision.commit_sha
            )
        if operation is RepositoryOperation.CHECKOUT:
            revision = await self._repositories.checkout(
                repository_id,
                _required_string(arguments, "revision"),
                context,
            )
            return _revision_output(
                revision.repository_id, revision.requested_ref, revision.commit_sha
            )
        if operation is RepositoryOperation.COMMIT:
            commit = await self._repositories.commit(
                repository_id,
                _required_string(arguments, "message"),
                context,
                author_name=_required_string(arguments, "author_name"),
                author_email=_required_string(arguments, "author_email"),
            )
            return {
                "repository_id": commit.repository_id,
                "revision": commit.revision,
                "message": commit.message,
                "parent_revisions": list(commit.parent_revisions),
            }
        if operation is RepositoryOperation.PUSH:
            revision = await self._repositories.push(
                repository_id,
                context,
                remote=_optional_string(arguments, "remote") or "origin",
                refspec=_optional_string(arguments, "refspec"),
            )
            return _revision_output(
                revision.repository_id, revision.requested_ref, revision.commit_sha
            )
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"repository capability is not implemented: {operation.value}",
            provider_id=self._provider_id,
        )

    async def _inspect_refs(
        self,
        repository_id: str,
        arguments: dict[str, JsonValue],
        context: RepositoryCallContext,
    ) -> dict[str, JsonValue]:
        kind = _required_string(arguments, "kind")
        if kind == "branches":
            return {
                "repository_id": repository_id,
                "branches": list(await self._repositories.branches(repository_id, context)),
            }
        if kind == "tags":
            return {
                "repository_id": repository_id,
                "tags": list(await self._repositories.tags(repository_id, context)),
            }
        if kind == "commits":
            limit = _optional_int(arguments, "limit")
            commits = await self._repositories.commits(
                repository_id,
                context,
                revision=_optional_string(arguments, "revision") or "HEAD",
                limit=50 if limit is None else limit,
            )
            return {
                "repository_id": repository_id,
                "commits": [
                    {
                        "repository_id": commit.repository_id,
                        "revision": commit.revision,
                        "message": commit.message,
                        "parent_revisions": list(commit.parent_revisions),
                    }
                    for commit in commits
                ],
            }
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "repository inspect_refs kind must be branches, tags or commits",
        )

    async def _write_issue(
        self,
        repository_id: str,
        arguments: dict[str, JsonValue],
        context: RepositoryCallContext,
    ) -> dict[str, JsonValue]:
        action = _required_string(arguments, "action")
        if action == "open":
            issue = await self._repositories.open_issue(
                repository_id,
                _required_string(arguments, "title"),
                context,
                body=_optional_text(arguments, "body"),
            )
            return issue.to_dict()
        if action == "update":
            resource = collaboration_reference_from_json(
                arguments.get("resource"),
                expected_resource_type="repository_issue",
            )
            issue = await self._repositories.update_issue(
                repository_id,
                resource,
                context,
                title=_optional_string(arguments, "title"),
                body=_optional_text(arguments, "body"),
                state=_optional_issue_state(arguments, "state"),
            )
            return issue.to_dict()
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "repository issue write action must be open or update",
        )

    async def _write_change_request(
        self,
        repository_id: str,
        arguments: dict[str, JsonValue],
        context: RepositoryCallContext,
    ) -> dict[str, JsonValue]:
        action = _required_string(arguments, "action")
        if action == "open":
            change_request = await self._repositories.open_change_request(
                repository_id,
                _required_string(arguments, "title"),
                _required_string(arguments, "head_ref"),
                _required_string(arguments, "base_ref"),
                context,
                body=_optional_text(arguments, "body"),
            )
            return change_request.to_dict()
        if action == "update":
            resource = collaboration_reference_from_json(
                arguments.get("resource"),
                expected_resource_type="repository_change_request",
            )
            change_request = await self._repositories.update_change_request(
                repository_id,
                resource,
                context,
                title=_optional_string(arguments, "title"),
                body=_optional_text(arguments, "body"),
                state=_optional_change_request_state(arguments, "state"),
            )
            return change_request.to_dict()
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "repository change-request write action must be open or update",
        )


def _input_schema(operation: RepositoryOperation) -> dict[str, JsonValue]:
    properties: dict[str, JsonValue] = {"repository_id": {"type": "string"}}
    required = ["repository_id"]
    if operation is RepositoryOperation.INSPECT_REFS:
        properties.update(
            {
                "kind": {"type": "string", "enum": ["branches", "tags", "commits"]},
                "revision": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }
        )
        required.append("kind")
    elif operation in {
        RepositoryOperation.ISSUE_READ,
        RepositoryOperation.CHANGE_REQUEST_READ,
    }:
        properties["resource"] = {"type": "object"}
        required.append("resource")
    elif operation is RepositoryOperation.ISSUE_WRITE:
        properties.update(
            {
                "action": {"type": "string", "enum": ["open", "update"]},
                "resource": {"type": "object"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "closed"]},
            }
        )
        required.append("action")
    elif operation is RepositoryOperation.CHANGE_REQUEST_WRITE:
        properties.update(
            {
                "action": {"type": "string", "enum": ["open", "update"]},
                "resource": {"type": "object"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "state": {
                    "type": "string",
                    "enum": ["open", "draft", "closed", "merged"],
                },
                "head_ref": {"type": "string"},
                "base_ref": {"type": "string"},
            }
        )
        required.append("action")
    elif operation is RepositoryOperation.DIFF:
        properties["base_revision"] = {"type": "string"}
    elif operation is RepositoryOperation.CREATE_BRANCH:
        properties.update(
            {
                "name": {"type": "string"},
                "start_revision": {"type": "string"},
                "checkout": {"type": "boolean"},
            }
        )
        required.append("name")
    elif operation is RepositoryOperation.CHECKOUT:
        properties["revision"] = {"type": "string"}
        required.append("revision")
    elif operation is RepositoryOperation.COMMIT:
        properties.update(
            {
                "message": {"type": "string"},
                "author_name": {"type": "string"},
                "author_email": {"type": "string"},
            }
        )
        required.extend(("message", "author_name", "author_email"))
    elif operation is RepositoryOperation.PUSH:
        properties.update({"remote": {"type": "string"}, "refspec": {"type": "string"}})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _required_string(arguments: dict[str, JsonValue], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository capability argument {key} must be a non-blank string",
        )
    return value


def _optional_string(arguments: dict[str, JsonValue], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository capability argument {key} must be a non-blank string or null",
        )
    return value


def _optional_text(arguments: dict[str, JsonValue], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository capability argument {key} must be a string or null",
        )
    return value


def _optional_bool(arguments: dict[str, JsonValue], key: str) -> bool | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository capability argument {key} must be boolean or null",
        )
    return value


def _optional_int(arguments: dict[str, JsonValue], key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository capability argument {key} must be integer or null",
        )
    return value


def _optional_issue_state(
    arguments: dict[str, JsonValue],
    key: str,
) -> RepositoryIssueState | None:
    value = _optional_string(arguments, key)
    if value is None:
        return None
    try:
        return RepositoryIssueState(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository capability argument {key} is not a valid issue state",
        ) from exc


def _optional_change_request_state(
    arguments: dict[str, JsonValue],
    key: str,
) -> RepositoryChangeRequestState | None:
    value = _optional_string(arguments, key)
    if value is None:
        return None
    try:
        return RepositoryChangeRequestState(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository capability argument {key} is not a valid change-request state",
        ) from exc


def _revision_output(
    repository_id: str, requested_ref: str, commit_sha: str
) -> dict[str, JsonValue]:
    return {
        "repository_id": repository_id,
        "requested_ref": requested_ref,
        "commit_sha": commit_sha,
    }
