"""Registration-based Control Plane extension for provider-neutral repositories."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .models import RepositoryCommit, RepositoryDiff, RepositoryReference, RepositoryRevision
from .service import RepositoryCallContext, RepositoryService

REPOSITORY_COLLECTION = "repositories"
REPOSITORY_COMMANDS = (
    "repository.status",
    "repository.diff",
    "repository.fetch",
    "repository.branch.create",
    "repository.checkout",
    "repository.commit",
    "repository.push",
)


class RepositoryResourceService(ResourceService):
    """Expose authorized canonical repository metadata through the #32 resource seam."""

    def __init__(self, repositories: RepositoryService) -> None:
        self._repositories = repositories

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        connection_id = None if query.filters is None else query.filters.get("connection_id")
        values = await self._repositories.list(
            _call_context(context),
            connection_id=connection_id,
        )
        return tuple(_repository_resource(value) for value in values)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        return _repository_resource(await self._repositories.read(resource_id, _call_context(context)))


def register_repository_control_plane(
    control_plane: ControlPlane,
    repositories: RepositoryService,
) -> None:
    """Register repository resources and policy-enforced Git commands.

    Every command delegates to ``RepositoryService`` so northbound clients cannot bypass the
    repository authorization/approval boundary by invoking provider adapters directly.
    """

    control_plane.register_resource_service(
        REPOSITORY_COLLECTION,
        RepositoryResourceService(repositories),
    )

    async def status(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"approval_id"})
        result = await repositories.status(resource_ref, _call_context(context, payload))
        return {
            "repository_id": result.repository_id,
            "head_revision": result.head_revision,
            "branch": result.branch,
            "clean": result.clean,
            "staged_paths": list(result.staged_paths),
            "modified_paths": list(result.modified_paths),
            "deleted_paths": list(result.deleted_paths),
            "untracked_paths": list(result.untracked_paths),
        }

    async def diff(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"base_revision", "approval_id"})
        result = await repositories.diff(
            resource_ref,
            _call_context(context, payload),
            base_revision=_optional_string(payload.get("base_revision"), "base_revision"),
        )
        return _diff_resource(result)

    async def fetch(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"approval_id"})
        result = await repositories.fetch(resource_ref, _call_context(context, payload))
        if result is None:
            return {"repository_id": resource_ref, "updated": False, "revision": None}
        value = _revision_resource(result)
        value["updated"] = True
        return value

    async def create_branch(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"name", "start_revision", "checkout", "approval_id"})
        result = await repositories.create_branch(
            resource_ref,
            _required_string(payload, "name"),
            _call_context(context, payload),
            start_revision=_optional_string(payload.get("start_revision"), "start_revision")
            or "HEAD",
            checkout=_optional_bool(payload.get("checkout"), "checkout") or False,
        )
        return _revision_resource(result)

    async def checkout(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"revision", "approval_id"})
        result = await repositories.checkout(
            resource_ref,
            _required_string(payload, "revision"),
            _call_context(context, payload),
        )
        return _revision_resource(result)

    async def commit(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"message", "author_name", "author_email", "approval_id"})
        result = await repositories.commit(
            resource_ref,
            _required_string(payload, "message"),
            _call_context(context, payload),
            author_name=_required_string(payload, "author_name"),
            author_email=_required_string(payload, "author_email"),
        )
        return _commit_resource(result)

    async def push(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"remote", "refspec", "approval_id"})
        result = await repositories.push(
            resource_ref,
            _call_context(context, payload),
            remote=_optional_string(payload.get("remote"), "remote") or "origin",
            refspec=_optional_string(payload.get("refspec"), "refspec"),
        )
        return _revision_resource(result)

    control_plane.register_command("repository.status", status)
    control_plane.register_command("repository.diff", diff)
    control_plane.register_command("repository.fetch", fetch)
    control_plane.register_command("repository.branch.create", create_branch)
    control_plane.register_command("repository.checkout", checkout)
    control_plane.register_command("repository.commit", commit)
    control_plane.register_command("repository.push", push)


def _call_context(
    request: RequestContext,
    payload: dict[str, JsonValue] | None = None,
) -> RepositoryCallContext:
    approval_id = None
    if payload is not None:
        approval_id = _optional_string(payload.get("approval_id"), "approval_id")
    return RepositoryCallContext(
        operation=OperationContext(
            correlation_id=request.correlation_id,
            owner_type=request.actor.owner_type,
            owner_id=request.actor.owner_id,
        ),
        actor_ref=request.actor.principal_ref,
        approval_id=approval_id,
    )


def _repository_resource(reference: RepositoryReference) -> dict[str, JsonValue]:
    return reference.to_dict()


def _revision_resource(revision: RepositoryRevision) -> dict[str, JsonValue]:
    return {
        "repository_id": revision.repository_id,
        "requested_ref": revision.requested_ref,
        "commit_sha": revision.commit_sha,
    }


def _diff_resource(diff: RepositoryDiff) -> dict[str, JsonValue]:
    return {
        "repository_id": diff.repository_id,
        "base_revision": diff.base_revision,
        "changed_paths": list(diff.changed_paths),
        "patch": diff.patch,
    }


def _commit_resource(commit: RepositoryCommit) -> dict[str, JsonValue]:
    return {
        "repository_id": commit.repository_id,
        "revision": commit.revision,
        "message": commit.message,
        "parent_revisions": list(commit.parent_revisions),
    }


def _reject_unknown(payload: dict[str, JsonValue], allowed: set[str]) -> None:
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "repository command payload contains unsupported fields",
            details={"fields": unexpected},
        )


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository command field {key} must be a non-blank string",
        )
    return value


def _optional_string(value: JsonValue, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository command field {field_name} must be a non-blank string or null",
        )
    return value


def _optional_bool(value: JsonValue, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository command field {field_name} must be boolean or null",
        )
    return value
