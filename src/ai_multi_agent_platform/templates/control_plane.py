"""Control Plane extension for reusable canonical Templates."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, json_object
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.workspaces import WorkspaceProvider

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
    def __init__(self, repository: TemplateRepository) -> None:
        self.repository = repository

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _template_resource(self.repository, item.template_id)
            for item in self.repository.list_templates()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _template_resource(self.repository, resource_id)


class TemplateInstanceResourceService:
    def __init__(self, repository: TemplateRepository) -> None:
        self.repository = repository

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_instance_resource(item) for item in self.repository.list_instantiations())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _instance_resource(self.repository.get_instantiation(resource_id))


class TemplateCommandHandlers:
    """Mutation/preview commands using server-resolved compatibility and permission state."""

    def __init__(
        self,
        application: TemplateApplicationService,
        *,
        environment_resolver: TemplateEnvironmentResolver | None = None,
        agent_exporter: AgentTemplateExporter | None = None,
        automation_exporter: AutomationTemplateExporter | None = None,
    ) -> None:
        self.application = application
        self.environment_resolver = environment_resolver
        self.agent_exporter = agent_exporter
        self.automation_exporter = automation_exporter

    async def create_template(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, TEMPLATE_COLLECTION)
        content = _authored_content(
            template_content_from_json(_required(payload, "content")),
            context,
            "template.create",
        )
        revision = self.application.templates.create_draft(
            owner_ref=_actor_owner(context),
            content=content,
            project_id=_optional_string(payload, "project_id"),
            organization_id=_optional_string(payload, "organization_id"),
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
        revision = self.agent_exporter.create_from_agent(
            _required_string(payload, "agent_id"),
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            revision=_optional_positive_int(payload, "revision"),
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
        revision = await self.automation_exporter.create_from_automation(
            _required_string(payload, "automation_id"),
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
        del context
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
    control_plane.register_resource_service(
        TEMPLATE_COLLECTION, TemplateResourceService(repository)
    )
    control_plane.register_resource_service(
        TEMPLATE_INSTANCE_COLLECTION,
        TemplateInstanceResourceService(repository),
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
