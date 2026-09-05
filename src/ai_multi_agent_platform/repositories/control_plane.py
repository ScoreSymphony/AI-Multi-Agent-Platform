"""Registration-based Control Plane extension for provider-neutral repositories."""

from __future__ import annotations

from urllib.parse import urlsplit

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .management import RepositoryManagementService
from .models import (
    RepositoryChangeRequestState,
    RepositoryCommit,
    RepositoryCommitInfo,
    RepositoryDiff,
    RepositoryIssueState,
    RepositoryOperation,
    RepositoryReference,
    RepositoryRevision,
)
from .references import collaboration_reference_from_json
from .service import RepositoryBinding, RepositoryCallContext, RepositoryService

REPOSITORY_COLLECTION = "repositories"
REPOSITORY_COMMANDS = (
    "repository.branches",
    "repository.tags",
    "repository.commits",
    "repository.issue.read",
    "repository.issue.open",
    "repository.issue.update",
    "repository.change_request.read",
    "repository.change_request.open",
    "repository.change_request.update",
    "repository.status",
    "repository.diff",
    "repository.fetch",
    "repository.branch.create",
    "repository.checkout",
    "repository.commit",
    "repository.push",
)
REPOSITORY_MANAGEMENT_COMMANDS = (
    "repository.local.attach",
    "repository.discover",
    "repository.detach",
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

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate privacy-minimized repository metadata for derived global Search.

        A Search rebuild cannot invent a privileged actor. The repository registry is the
        canonical attached-resource inventory, so rebuilding from its current bindings makes
        detach/replacement reconstructable without invoking provider APIs or exposing adapter
        state. Per-result Control Plane authorization is still applied by Search before counts,
        snippets or exact existence are returned.
        """

        # RepositoryResourceService and RepositoryService are one domain boundary. Reading the
        # internal registry here mirrors the connector search-rebuild seam while deliberately
        # avoiding provider reads and synthetic authorization identities.
        bindings = self._repositories._registry.list()  # noqa: SLF001
        return tuple(_repository_search_resource(binding) for binding in bindings)

    async def search_result_allowed(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> bool:
        """Re-evaluate canonical Repository policy without provider reads or content access."""

        try:
            binding = self._repositories._registry.resolve(resource_id)  # noqa: SLF001
            await self._repositories._enforce(  # noqa: SLF001
                binding,
                RepositoryOperation.READ,
                _call_context(context),
            )
        except ContractError as exc:
            if exc.code in {ErrorCode.NOT_FOUND, ErrorCode.UNAUTHORIZED, ErrorCode.FORBIDDEN}:
                return False
            raise
        return True

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        return _repository_resource(
            await self._repositories.read(resource_id, _call_context(context))
        )


def register_repository_control_plane(
    control_plane: ControlPlane,
    repositories: RepositoryService,
    *,
    management: RepositoryManagementService | None = None,
) -> None:
    """Register repository resources and policy-enforced repository commands.

    Every command delegates to ``RepositoryService`` or ``RepositoryManagementService`` so
    northbound clients cannot bypass the repository authorization/approval boundary by invoking
    provider adapters directly.
    """

    control_plane.register_resource_service(
        REPOSITORY_COLLECTION,
        RepositoryResourceService(repositories),
    )

    async def branches(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"approval_id"})
        values = await repositories.branches(resource_ref, _call_context(context, payload))
        return {"repository_id": resource_ref, "branches": list(values)}

    async def tags(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"approval_id"})
        values = await repositories.tags(resource_ref, _call_context(context, payload))
        return {"repository_id": resource_ref, "tags": list(values)}

    async def commits(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"revision", "limit", "approval_id"})
        limit = _optional_int(payload.get("limit"), "limit")
        values = await repositories.commits(
            resource_ref,
            _call_context(context, payload),
            revision=_optional_string(payload.get("revision"), "revision") or "HEAD",
            limit=50 if limit is None else limit,
        )
        return {
            "repository_id": resource_ref,
            "commits": [_commit_info_resource(value) for value in values],
        }

    async def read_issue(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"resource", "approval_id"})
        issue = collaboration_reference_from_json(
            payload.get("resource"),
            expected_resource_type="repository_issue",
        )
        return (
            await repositories.read_issue(
                resource_ref,
                issue,
                _call_context(context, payload),
            )
        ).to_dict()

    async def open_issue(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"title", "body", "approval_id"})
        return (
            await repositories.open_issue(
                resource_ref,
                _required_string(payload, "title"),
                _call_context(context, payload),
                body=_optional_text(payload.get("body"), "body"),
            )
        ).to_dict()

    async def update_issue(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"resource", "title", "body", "state", "approval_id"})
        issue = collaboration_reference_from_json(
            payload.get("resource"),
            expected_resource_type="repository_issue",
        )
        return (
            await repositories.update_issue(
                resource_ref,
                issue,
                _call_context(context, payload),
                title=_optional_string(payload.get("title"), "title"),
                body=_optional_text(payload.get("body"), "body"),
                state=_optional_issue_state(payload.get("state")),
            )
        ).to_dict()

    async def read_change_request(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"resource", "approval_id"})
        change_request = collaboration_reference_from_json(
            payload.get("resource"),
            expected_resource_type="repository_change_request",
        )
        return (
            await repositories.read_change_request(
                resource_ref,
                change_request,
                _call_context(context, payload),
            )
        ).to_dict()

    async def open_change_request(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(
            payload,
            {"title", "head_ref", "base_ref", "body", "approval_id"},
        )
        return (
            await repositories.open_change_request(
                resource_ref,
                _required_string(payload, "title"),
                _required_string(payload, "head_ref"),
                _required_string(payload, "base_ref"),
                _call_context(context, payload),
                body=_optional_text(payload.get("body"), "body"),
            )
        ).to_dict()

    async def update_change_request(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"resource", "title", "body", "state", "approval_id"})
        change_request = collaboration_reference_from_json(
            payload.get("resource"),
            expected_resource_type="repository_change_request",
        )
        return (
            await repositories.update_change_request(
                resource_ref,
                change_request,
                _call_context(context, payload),
                title=_optional_string(payload.get("title"), "title"),
                body=_optional_text(payload.get("body"), "body"),
                state=_optional_change_request_state(payload.get("state")),
            )
        ).to_dict()

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

    control_plane.register_command("repository.branches", branches)
    control_plane.register_command("repository.tags", tags)
    control_plane.register_command("repository.commits", commits)
    control_plane.register_command("repository.issue.read", read_issue)
    control_plane.register_command("repository.issue.open", open_issue)
    control_plane.register_command("repository.issue.update", update_issue)
    control_plane.register_command("repository.change_request.read", read_change_request)
    control_plane.register_command("repository.change_request.open", open_change_request)
    control_plane.register_command("repository.change_request.update", update_change_request)
    control_plane.register_command("repository.status", status)
    control_plane.register_command("repository.diff", diff)
    control_plane.register_command("repository.fetch", fetch)
    control_plane.register_command("repository.branch.create", create_branch)
    control_plane.register_command("repository.checkout", checkout)
    control_plane.register_command("repository.commit", commit)
    control_plane.register_command("repository.push", push)

    if management is None:
        return

    async def attach_local(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(
            payload,
            {"name", "initialize", "default_branch", "approval_id"},
        )
        reference = await management.attach_local(
            _required_string(payload, "name"),
            _call_context(context, payload, project_id=resource_ref),
            initialize=_optional_bool(payload.get("initialize"), "initialize") or False,
            default_branch=_optional_string(payload.get("default_branch"), "default_branch")
            or "main",
        )
        return _repository_resource(reference)

    async def discover(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"provider_id", "attach", "approval_id"})
        provider_id = _required_string(payload, "provider_id")
        attach = _optional_bool(payload.get("attach"), "attach") or False
        call_context = _call_context(context, payload)
        if attach:
            references = await management.discover_and_attach(
                resource_ref,
                provider_id,
                call_context,
            )
        else:
            references = await management.discover(
                resource_ref,
                provider_id,
                call_context,
            )
        return {
            "connection_id": resource_ref,
            "provider_id": provider_id,
            "attached": attach,
            "repositories": [_repository_resource(reference) for reference in references],
        }

    async def detach(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"approval_id"})
        reference = await management.detach(
            resource_ref,
            _call_context(context, payload),
        )
        return {
            "repository_id": reference.id,
            "detached": True,
            "provider_content_deleted": False,
        }

    control_plane.register_command("repository.local.attach", attach_local)
    control_plane.register_command("repository.discover", discover)
    control_plane.register_command("repository.detach", detach)


def _call_context(
    request: RequestContext,
    payload: dict[str, JsonValue] | None = None,
    *,
    project_id: str | None = None,
) -> RepositoryCallContext:
    approval_id = None
    if payload is not None:
        approval_id = _optional_string(payload.get("approval_id"), "approval_id")
    return RepositoryCallContext(
        operation=OperationContext(
            correlation_id=request.correlation_id,
            owner_type=request.actor.owner_type,
            owner_id=request.actor.owner_id,
            project_id=project_id,
        ),
        actor_ref=request.actor.principal_ref,
        approval_id=approval_id,
    )


def _repository_resource(reference: RepositoryReference) -> dict[str, JsonValue]:
    return reference.to_dict()


def _repository_search_resource(binding: RepositoryBinding) -> dict[str, JsonValue]:
    """Return safe canonical Repository metadata suitable for the derived Search index."""

    reference = binding.reference
    connection = binding.connection.connection
    host = _repository_url_host(reference.external_resource.canonical_url)
    name = _repository_search_name(binding, host)
    revision = (
        reference.resolved_revision
        or reference.external_resource.revision
        or reference.target_revision
    )
    aliases = tuple(
        value
        for value in (
            host,
            reference.default_branch,
            reference.target_revision,
            reference.resolved_revision,
        )
        if value is not None
    )
    operations = tuple(
        capability.operation.value for capability in reference.capabilities if capability.supported
    )
    summary_parts = [f"{reference.visibility.value} repository"]
    if host is not None:
        summary_parts.append(f"host {host}")
    if reference.default_branch is not None:
        summary_parts.append(f"default branch {reference.default_branch}")
    if revision is not None:
        summary_parts.append(f"revision {revision}")

    return {
        "id": reference.id,
        "type": "repository",
        "name": name,
        "summary": "; ".join(summary_parts),
        "owner_type": connection.owner_type,
        "owner_id": connection.owner_id,
        "project_id": connection.project_id,
        "organization_id": connection.organization_id,
        "status": reference.visibility.value,
        "revision": revision,
        "aliases": list(dict.fromkeys(aliases)),
        "capabilities": list(dict.fromkeys(operations)),
    }


def _repository_search_name(binding: RepositoryBinding, host: str | None) -> str:
    connection = binding.connection.connection
    reference = binding.reference
    if binding.connection.local:
        return connection.display_name

    canonical_url = reference.external_resource.canonical_url
    if canonical_url is not None and host is not None:
        parsed = urlsplit(canonical_url)
        path = parsed.path.rstrip("/")
        if path:
            candidate = path.rsplit("/", 1)[-1]
            if candidate.endswith(".git"):
                candidate = candidate[:-4]
            if candidate:
                return candidate

    return connection.display_name


def _repository_url_host(canonical_url: str | None) -> str | None:
    if canonical_url is None:
        return None
    parsed = urlsplit(canonical_url)
    if parsed.hostname is not None:
        return parsed.hostname.lower()

    # Support the common SCP-like Git form (git@host:owner/repository.git) without
    # retaining the path or user portion. Local/file URLs intentionally produce no host.
    if "://" not in canonical_url and "@" in canonical_url:
        host_and_path = canonical_url.rsplit("@", 1)[-1]
        host, separator, _ = host_and_path.partition(":")
        if separator and host.strip():
            return host.strip().lower()
    return None


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


def _commit_info_resource(commit: RepositoryCommitInfo) -> dict[str, JsonValue]:
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
            details={"fields": ",".join(unexpected)},
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


def _optional_text(value: JsonValue, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository command field {field_name} must be string or null",
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


def _optional_int(value: JsonValue, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository command field {field_name} must be integer or null",
        )
    return value


def _optional_issue_state(value: JsonValue) -> RepositoryIssueState | None:
    raw = _optional_string(value, "state")
    if raw is None:
        return None
    try:
        return RepositoryIssueState(raw)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "repository command field state is not a valid issue state",
        ) from exc


def _optional_change_request_state(value: JsonValue) -> RepositoryChangeRequestState | None:
    raw = _optional_string(value, "state")
    if raw is None:
        return None
    try:
        return RepositoryChangeRequestState(raw)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "repository command field state is not a valid change-request state",
        ) from exc