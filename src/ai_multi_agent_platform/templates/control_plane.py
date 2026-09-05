"""Control Plane extension for reusable canonical Templates."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import (
    OwnerType,
    PageQuery,
    RequestContext,
    json_object,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.workspaces import WorkspaceProvider

from .access import TemplateScopeAccess
from .agent_handlers import AgentTemplateExporter
from .application import TemplateApplicationService
from .automation_handler import AutomationTemplateExporter
from .codec import template_content_from_json
from .environment import PlatformTemplateEnvironmentResolver
from .models import TemplateContent, TemplateProvenance, TemplateTrust
from .repository import TemplateRepository
from .service import TemplateEnvironment

TEMPLATE_COLLECTION = "templates"
TEMPLATE_INSTANCE_COLLECTION = "template-instances"
TEMPLATE_COMMANDS = (
    "template.create",
    "template.create-from-agent",
    "template.create-from-automation",
    "template.revise",
    "template.publish",
    "template.clone",
    "template.fork",
    "template.preview",
    "template.apply",
    "template.reapply",
)

_SERVER_RESOLVED_ENVIRONMENT_FIELDS = frozenset(
    {
        "environment",
        "capability_ids",
        "plugin_ids",
        "connector_ids",
        "model_policy_refs",
        "grantable_permissions",
        "workspace_prerequisites",
        "resolved_placeholders",
        "resolved_secret_reference_placeholders",
        "validated_configuration_refs",
    }
)


class TemplateEnvironmentResolver(Protocol):
    """Resolve trusted compatibility/application inputs from server-side platform state."""

    async def resolve(self, context: RequestContext) -> TemplateEnvironment: ...


class TemplateResourceService:
    def __init__(
        self,
        repository: TemplateRepository,
        *,
        scope_access: TemplateScopeAccess | None = None,
    ) -> None:
        self.repository = repository
        self.scope_access = scope_access

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for item in self.repository.list_templates():
            if self.scope_access is not None and not await self.scope_access.allowed(
                context,
                "template:list",
                item.template_id,
                owner_ref=item.owner_ref,
                project_id=item.project_id,
            ):
                continue
            resources.append(_template_resource(self.repository, item.template_id))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        definition = self.repository.get_template(resource_id)
        if self.scope_access is not None:
            await self.scope_access.authorize(
                context,
                "template:read",
                resource_id,
                owner_ref=definition.owner_ref,
                project_id=definition.project_id,
            )
        return _template_resource(self.repository, resource_id)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Return privacy-safe metadata for the derived global Search index."""

        resources: list[dict[str, JsonValue]] = []
        for item in self.repository.list_templates():
            resource = _template_search_resource(self.repository, item.template_id)
            if resource is not None:
                resources.append(resource)
        return tuple(resources)


class TemplateInstanceResourceService:
    search_indexable = False

    def __init__(
        self,
        repository: TemplateRepository,
        *,
        scope_access: TemplateScopeAccess | None = None,
    ) -> None:
        self.repository = repository
        self.scope_access = scope_access

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for item in self.repository.list_instantiations():
            if self.scope_access is not None and not await self.scope_access.allowed(
                context,
                "template-instance:list",
                item.instance_id,
                owner_ref=item.applied_by,
            ):
                continue
            resources.append(_instance_resource(item))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        item = self.repository.get_instantiation(resource_id)
        if self.scope_access is not None:
            await self.scope_access.authorize(
                context,
                "template-instance:read",
                resource_id,
                owner_ref=item.applied_by,
            )
        return _instance_resource(item)


class TemplateCommandHandlers:
    """Mutation/preview commands using server-resolved compatibility and permission state."""

    def __init__(
        self,
        application: TemplateApplicationService,
        *,
        environment_resolver: TemplateEnvironmentResolver | None = None,
        agent_exporter: AgentTemplateExporter | None = None,
        automation_exporter: AutomationTemplateExporter | None = None,
        scope_access: TemplateScopeAccess | None = None,
    ) -> None:
        self.application = application
        self.environment_resolver = environment_resolver
        self.agent_exporter = agent_exporter
        self.automation_exporter = automation_exporter
        self.scope_access = scope_access

    async def create_template(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, TEMPLATE_COLLECTION)
        organization_id = _optional_string(payload, "organization_id")
        _validate_requested_organization_scope(context, organization_id)
        content = _authored_content(
            template_content_from_json(_required(payload, "content")),
            context,
            "template.create",
        )
        revision = self.application.templates.create_draft(
            owner_ref=_actor_owner(context),
            content=content,
            project_id=_optional_string(payload, "project_id"),
            organization_id=organization_id,
        )
        return _template_resource(self.application.repository, revision.template_id)

    async def create_from_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, TEMPLATE_COLLECTION)
        if self.agent_exporter is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Agent-to-Template export is not enabled in this Control Plane composition",
            )
        agent_id = _required_string(payload, "agent_id")
        source_revision = _optional_positive_int(payload, "revision")
        if self.scope_access is not None:
            source = self.agent_exporter.agents.get_agent_revision(agent_id, source_revision)
            await self.scope_access.authorize(
                context,
                "template.create-from-agent",
                source.agent_id,
                owner_ref=source.owner_ref,
                project_id=source.project_id,
            )
        revision = self.agent_exporter.create_from_agent(
            agent_id,
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            revision=source_revision,
            name=_optional_string(payload, "name"),
        )
        return _template_resource(self.application.repository, revision.template_id)

    async def create_from_automation(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, TEMPLATE_COLLECTION)
        if self.automation_exporter is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Automation-to-Template export is not enabled in this Control Plane composition",
            )
        automation_id = _required_string(payload, "automation_id")
        if self.scope_access is not None:
            source = await self.automation_exporter.automations.get_automation(automation_id)
            owner_type = source.identity.owner_type
            if owner_type not in {"user", "organization", "team", "service"}:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Automation owner type is not a canonical owner type",
                )
            await self.scope_access.authorize(
                context,
                "template.create-from-automation",
                source.id,
                owner_ref=OwnerRef(
                    type=cast(OwnerType, owner_type),
                    id=source.identity.owner_id,
                ),
                project_id=source.project_id,
            )
        revision = await self.automation_exporter.create_from_automation(
            automation_id,
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            name=_optional_string(payload, "name"),
        )
        return _template_resource(self.application.repository, revision.template_id)

    async def revise_template(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_template(context, "template.revise", resource_ref)
        content = _authored_content(
            template_content_from_json(_required(payload, "content")),
            context,
            "template.revise",
        )
        revision = self.application.templates.revise_draft(
            resource_ref,
            content,
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return _template_resource(self.application.repository, revision.template_id)

    async def publish_template(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_template(context, "template.publish", resource_ref)
        revision = self.application.templates.publish(
            resource_ref,
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return _template_resource(self.application.repository, revision.template_id)

    async def clone_template(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_template(context, "template.clone", resource_ref)
        revision = self.application.templates.clone_template(
            resource_ref,
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            revision=_optional_positive_int(payload, "revision"),
            name=_optional_string(payload, "name"),
        )
        return _template_resource(self.application.repository, revision.template_id)

    async def fork_template(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_template(context, "template.fork", resource_ref)
        revision = self.application.templates.fork_template(
            resource_ref,
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            revision=_optional_positive_int(payload, "revision"),
            name=_optional_string(payload, "name"),
        )
        return _template_resource(self.application.repository, revision.template_id)

    async def preview_template(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_template(context, "template.preview", resource_ref)
        _reject_server_resolved_environment(payload)
        preview = self.application.preview(
            resource_ref,
            applied_by=_actor_owner(context),
            environment=await self._environment(context),
            revision=_optional_positive_int(payload, "revision"),
            allow_draft=_optional_bool(payload, "allow_draft", default=False),
        )
        result = json_object(preview)
        result["applicable"] = preview.applicable
        return result

    async def apply_template(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_template(context, "template.apply", resource_ref)
        _reject_server_resolved_environment(payload)
        instantiation = await self.application.apply(
            resource_ref,
            applied_by=_actor_owner(context),
            environment=await self._environment(context),
            revision=_optional_positive_int(payload, "revision"),
        )
        return _instance_resource(instantiation)

    async def reapply_template(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_template(context, "template.reapply", resource_ref)
        _reject_server_resolved_environment(payload)
        instantiation = await self.application.reapply(
            resource_ref,
            applied_by=_actor_owner(context),
            environment=await self._environment(context),
            revision=_optional_positive_int(payload, "revision"),
        )
        return _instance_resource(instantiation)

    async def _environment(self, context: RequestContext) -> TemplateEnvironment:
        if self.environment_resolver is None:
            return TemplateEnvironment()
        return await self.environment_resolver.resolve(context)

    async def _authorize_template(
        self,
        context: RequestContext,
        action: str,
        template_id: str,
    ) -> None:
        if self.scope_access is None:
            return
        definition = self.application.repository.get_template(template_id)
        await self.scope_access.authorize(
            context,
            action,
            template_id,
            owner_ref=definition.owner_ref,
            project_id=definition.project_id,
        )


def register_template_control_plane(
    control_plane: ControlPlane,
    application: TemplateApplicationService,
    *,
    environment_resolver: TemplateEnvironmentResolver | None = None,
    agent_exporter: AgentTemplateExporter | None = None,
    automation_exporter: AutomationTemplateExporter | None = None,
) -> None:
    """Register Template resources/commands without changing the Control Plane foundation."""

    repository = application.repository
    scope_access = TemplateScopeAccess(control_plane)
    control_plane.register_resource_service(
        TEMPLATE_COLLECTION,
        TemplateResourceService(repository, scope_access=scope_access),
    )
    control_plane.register_resource_service(
        TEMPLATE_INSTANCE_COLLECTION,
        TemplateInstanceResourceService(repository, scope_access=scope_access),
    )
    if environment_resolver is None:
        workspace_provider = cast(
            WorkspaceProvider | None,
            getattr(control_plane, "workspace_provider", None),
        )
        environment_resolver = PlatformTemplateEnvironmentResolver(workspaces=workspace_provider)
    handlers = TemplateCommandHandlers(
        application,
        environment_resolver=environment_resolver,
        agent_exporter=agent_exporter,
        automation_exporter=automation_exporter,
        scope_access=scope_access,
    )
    control_plane.register_command("template.create", handlers.create_template)
    if agent_exporter is not None:
        control_plane.register_command("template.create-from-agent", handlers.create_from_agent)
    if automation_exporter is not None:
        control_plane.register_command(
            "template.create-from-automation",
            handlers.create_from_automation,
        )
    control_plane.register_command("template.revise", handlers.revise_template)
    control_plane.register_command("template.publish", handlers.publish_template)
    control_plane.register_command("template.clone", handlers.clone_template)
    control_plane.register_command("template.fork", handlers.fork_template)
    control_plane.register_command("template.preview", handlers.preview_template)
    control_plane.register_command("template.apply", handlers.apply_template)
    control_plane.register_command("template.reapply", handlers.reapply_template)


def _template_resource(
    repository: TemplateRepository,
    template_id: str,
) -> dict[str, JsonValue]:
    definition = repository.get_template(template_id)
    revision = repository.get_revision(template_id, definition.current_revision)
    return {
        "id": template_id,
        "type": "template",
        "current_revision": definition.current_revision,
        "latest_published_revision": definition.latest_published_revision,
        "owner_ref": json_object(definition.owner_ref),
        "project_id": definition.project_id,
        "organization_id": definition.organization_id,
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        "revision": json_object(revision),
        "revisions": [json_object(item) for item in repository.list_revisions(template_id)],
    }


def _template_search_resource(
    repository: TemplateRepository,
    template_id: str,
) -> dict[str, JsonValue] | None:
    definition = repository.get_template(template_id)
    if definition.organization_id is not None and (
        definition.owner_ref.type != "organization"
        or definition.owner_ref.id != definition.organization_id
    ):
        # Search must fail closed when an organization scope cannot be represented by
        # the canonical owner scope consumed by registered-resource authorization.
        return None
    revision = repository.get_revision(template_id, definition.current_revision)
    content = revision.content
    dependencies: list[JsonValue] = [
        (
            dependency.template_id
            if dependency.revision is None
            else f"{dependency.template_id}@{dependency.revision}"
        )
        for dependency in content.dependencies
    ]
    return {
        "id": template_id,
        "type": "template",
        "name": content.name,
        "description": content.description,
        "kind": content.template_type.value,
        "state": revision.state.value,
        "current_revision": definition.current_revision,
        "latest_published_revision": definition.latest_published_revision,
        "owner_ref": json_object(definition.owner_ref),
        "project_id": definition.project_id,
        "organization_id": definition.organization_id,
        "updated_at": definition.updated_at.isoformat(),
        "tags": list(content.tags),
        "dependencies": dependencies,
        "source": content.provenance.source,
        "author": content.provenance.author,
    }


def _instance_resource(instantiation: object) -> dict[str, JsonValue]:
    item = json_object(instantiation)
    instance_id = item.get("instance_id")
    if not isinstance(instance_id, str):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Template instantiation serialization lost its canonical ID",
        )
    return {
        "id": instance_id,
        "type": "template-instance",
        **item,
    }


def _authored_content(
    content: TemplateContent,
    context: RequestContext,
    source: str,
) -> TemplateContent:
    return replace(
        content,
        provenance=TemplateProvenance(
            author=context.actor.principal_ref,
            source=f"control-plane:{source}",
            trust=TemplateTrust.LOCAL,
        ),
    )


def _actor_owner(context: RequestContext) -> OwnerRef:
    if context.actor.owner_type is not None and context.actor.owner_id is not None:
        return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)
    return OwnerRef(type="service", id=context.actor.principal_ref)


def _validate_requested_organization_scope(
    context: RequestContext,
    organization_id: str | None,
) -> None:
    if organization_id is None:
        return
    owner = _actor_owner(context)
    if owner.type != "organization" or owner.id != organization_id:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "organization-scoped Templates must be created under the matching organization owner",
        )


def _reject_server_resolved_environment(payload: dict[str, JsonValue]) -> None:
    supplied = sorted(_SERVER_RESOLVED_ENVIRONMENT_FIELDS.intersection(payload))
    if supplied:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Template compatibility and authorization environment is server-resolved",
            details={"fields": cast(JsonValue, supplied)},
        )


def _require_collection(resource_ref: str, collection: str) -> None:
    if resource_ref != collection:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"command must target collection {collection!r}",
        )


def _required(payload: dict[str, JsonValue], key: str) -> JsonValue:
    if key not in payload:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"missing required field: {key}")
    return payload[key]


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = _required(payload, key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _required_positive_int(payload: dict[str, JsonValue], key: str) -> int:
    value = _required(payload, key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a positive integer")
    return value


def _optional_positive_int(payload: dict[str, JsonValue], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a positive integer")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_bool(
    payload: dict[str, JsonValue],
    key: str,
    *,
    default: bool,
) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a boolean")
    return value
