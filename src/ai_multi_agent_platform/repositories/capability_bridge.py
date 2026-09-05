"""Bridge canonical repository operations into the #12 capability invocation path."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.capabilities import CapabilityRegistration, CapabilityToolProvider
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
from .service import RepositoryCallContext, RepositoryService

RepositoryActorResolver = Callable[[OperationContext], str]

_TOOL_OPERATIONS = frozenset(
    {
        RepositoryOperation.READ,
        RepositoryOperation.STATUS,
        RepositoryOperation.DIFF,
        RepositoryOperation.FETCH,
        RepositoryOperation.CREATE_BRANCH,
        RepositoryOperation.CHECKOUT,
        RepositoryOperation.COMMIT,
        RepositoryOperation.PUSH,
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
        specs = {
            spec.capability_id: spec
            for spec in repository_capability_specs()
            if spec.capability_id in {operation.value for operation in _TOOL_OPERATIONS}
        }
        registrations: list[CapabilityRegistration] = []
        for operation in sorted(_TOOL_OPERATIONS, key=lambda value: value.value):
            spec = specs[operation.value]
            schema = _input_schema(operation)
            registrations.append(
                CapabilityRegistration(
                    capability=type(spec)(
                        capability_id=spec.capability_id,
                        name=spec.name,
                        version=spec.version,
                        description=spec.description,
                        input_schema=schema,
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
            return _revision_output(revision.repository_id, revision.requested_ref, revision.commit_sha)
        if operation is RepositoryOperation.CHECKOUT:
            revision = await self._repositories.checkout(
                repository_id,
                _required_string(arguments, "revision"),
                context,
            )
            return _revision_output(revision.repository_id, revision.requested_ref, revision.commit_sha)
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
            return _revision_output(revision.repository_id, revision.requested_ref, revision.commit_sha)
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"repository capability is not implemented: {operation.value}",
            provider_id=self._provider_id,
        )


def _input_schema(operation: RepositoryOperation) -> dict[str, JsonValue]:
    properties: dict[str, JsonValue] = {"repository_id": {"type": "string"}}
    required = ["repository_id"]
    if operation is RepositoryOperation.DIFF:
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


def _revision_output(repository_id: str, requested_ref: str, commit_sha: str) -> dict[str, JsonValue]:
    return {
        "repository_id": repository_id,
        "requested_ref": requested_ref,
        "commit_sha": commit_sha,
    }
