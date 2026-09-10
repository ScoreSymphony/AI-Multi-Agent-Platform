"""Versioned Control Plane projection for application distribution."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, json_object
from ai_multi_agent_platform.security import ActorIdentity, ActorType, infer_actor_identity

from .contracts import PublishContext
from .models import (
    BuildSpecification,
    BuildTarget,
    PackageType,
    ReleaseChannel,
    ReleaseVisibility,
)
from .service import ApplicationDistributionService

APPLICATION_RELEASE_COLLECTION = "application-releases"
APPLICATION_RELEASE_COMMANDS = (
    "application-release.create",
    "application-release.build",
    "application-release.preview",
    "application-release.publish",
)


class ApplicationReleaseResourceService:
    search_indexable = False

    def __init__(self, service: ApplicationDistributionService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            json_object(release) | {"id": release.release_id, "type": "application_release"}
            for release in await self.service.repository.list()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        release = await self.service.repository.get(resource_id)
        return json_object(release) | {
            "id": release.release_id,
            "type": "application_release",
        }


class ApplicationReleaseCommandHandlers:
    def __init__(self, service: ApplicationDistributionService) -> None:
        self.service = service

    async def create(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != APPLICATION_RELEASE_COLLECTION:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "create requires application-releases resource_ref",
            )
        try:
            channel = ReleaseChannel(_required_string(payload, "channel"))
            visibility = ReleaseVisibility(_required_string(payload, "visibility"))
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"invalid application release enum value: {exc}",
            ) from exc
        release = await self.service.create_release(
            application_id=_required_string(payload, "application_id"),
            display_name=_required_string(payload, "display_name"),
            version=_required_string(payload, "version"),
            channel=channel,
            visibility=visibility,
            project_id=_required_string(payload, "project_id"),
            workspace_id=_required_string(payload, "workspace_id"),
            workspace_snapshot_id=_optional_string(payload, "workspace_snapshot_id"),
            source_revision=_required_string(payload, "source_revision"),
            build_specification=_build_spec(
                payload.get("build_specification"),
                default_spec_id=_default_build_spec_id(context),
            ),
            creator_ref=context.actor.principal_ref,
            release_notes=_optional_string(payload, "release_notes"),
            previous_release_id=_optional_string(payload, "previous_release_id"),
        )
        return _resource(release)

    async def build(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        release = await self.service.request_build(
            resource_ref,
            target_id=_required_string(payload, "target_id"),
            idempotency_key=context.idempotency_key or context.request_id,
            actor_ref=context.actor.principal_ref,
            approval_id=_optional_string(payload, "approval_id"),
        )
        return _resource(release)

    async def preview(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        preview = await self.service.preview_publication(
            resource_ref,
            publisher_id=_required_string(payload, "publisher_id"),
            context=_publish_context(
                context,
                _optional_string(payload, "approval_id"),
                _json_object(
                    payload.get("publisher_configuration"),
                    "publisher_configuration",
                ),
            ),
        )
        return {
            "id": resource_ref,
            "type": "application_release_preview",
            **preview,
        }

    async def publish(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        release = await self.service.publish(
            resource_ref,
            publisher_id=_required_string(payload, "publisher_id"),
            context=_publish_context(
                context,
                _optional_string(payload, "approval_id"),
                _json_object(
                    payload.get("publisher_configuration"),
                    "publisher_configuration",
                ),
            ),
        )
        return _resource(release)


def register_application_distribution_control_plane(
    control_plane: ControlPlane,
    service: ApplicationDistributionService,
) -> None:
    if APPLICATION_RELEASE_COLLECTION not in control_plane.registered_collections:
        control_plane.register_resource_service(
            APPLICATION_RELEASE_COLLECTION,
            ApplicationReleaseResourceService(service),
        )
    handlers = ApplicationReleaseCommandHandlers(service)
    registered = set(control_plane.registered_commands)
    for command, handler in (
        (APPLICATION_RELEASE_COMMANDS[0], handlers.create),
        (APPLICATION_RELEASE_COMMANDS[1], handlers.build),
        (APPLICATION_RELEASE_COMMANDS[2], handlers.preview),
        (APPLICATION_RELEASE_COMMANDS[3], handlers.publish),
    ):
        if command not in registered:
            control_plane.register_command(command, handler)


def _resource(release: object) -> dict[str, JsonValue]:
    resource = json_object(release)
    release_id = resource.get("release_id")
    if not isinstance(release_id, str):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "release projection has no release_id",
        )
    resource["id"] = release_id
    resource["type"] = "application_release"
    return resource


def _publish_context(
    context: RequestContext,
    approval_id: str | None,
    configuration: dict[str, JsonValue],
) -> PublishContext:
    try:
        return PublishContext(
            actor=_actor(context),
            operation=OperationContext(
                correlation_id=context.correlation_id,
                causation_id=context.request_id,
                owner_type=context.actor.owner_type,
                owner_id=context.actor.owner_id,
            ),
            approval_id=approval_id,
            configuration=configuration,
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"invalid publisher configuration: {exc}",
        ) from exc


def _actor(context: RequestContext) -> ActorIdentity:
    if context.actor.actor_type is None:
        return infer_actor_identity(context.actor.principal_ref)
    try:
        return ActorIdentity(
            context.actor.principal_ref,
            ActorType(context.actor.actor_type),
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "unsupported authenticated actor type",
        ) from exc


def _default_build_spec_id(context: RequestContext) -> str:
    operation_key = context.idempotency_key or context.request_id
    value = uuid5(
        NAMESPACE_URL,
        f"ai-multi-agent-platform:application-release:build-spec:{operation_key}",
    )
    return f"build_spec_{value}"


def _build_spec(
    value: JsonValue | None,
    *,
    default_spec_id: str,
) -> BuildSpecification:
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "build_specification must be an object",
        )
    command = _string_tuple(value.get("command"), "command")
    raw_targets = value.get("targets")
    if not isinstance(raw_targets, list):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "build_specification.targets must be an array",
        )
    targets: list[BuildTarget] = []
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "each build target must be an object",
            )
        try:
            targets.append(
                BuildTarget(
                    target_id=_required_string(raw, "target_id"),
                    os_name=_required_string(raw, "os_name"),
                    architecture=_required_string(raw, "architecture"),
                    package_type=PackageType(_required_string(raw, "package_type")),
                    output_path=_required_string(raw, "output_path"),
                    required_capabilities=_string_tuple(
                        raw.get("required_capabilities"),
                        "required_capabilities",
                    ),
                )
            )
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"invalid build target: {exc}",
            ) from exc
    try:
        return BuildSpecification(
            command=command,
            targets=tuple(targets),
            spec_id=_optional_string(value, "spec_id") or default_spec_id,
            revision=_positive_int(value.get("revision", 1), "revision"),
            source_path=_optional_string(value, "source_path"),
            workflow_ref=_optional_string(value, "workflow_ref"),
            pre_build_checks=_string_tuple(
                value.get("pre_build_checks"),
                "pre_build_checks",
            ),
            test_gates=_string_tuple(value.get("test_gates"), "test_gates"),
            post_build_checks=_string_tuple(
                value.get("post_build_checks"),
                "post_build_checks",
            ),
            required_capabilities=_string_tuple(
                value.get("required_capabilities"),
                "required_capabilities",
            ),
            resource_hints=_json_object(
                value.get("resource_hints"),
                "resource_hints",
            ),
            secret_references=_string_tuple(
                value.get("secret_references"),
                "secret_references",
            ),
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"invalid build specification: {exc}",
        ) from exc


def _required_string(payload: dict[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be a non-blank string",
        )
    return value


def _optional_string(payload: dict[str, JsonValue], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be a non-blank string",
        )
    return value


def _string_tuple(value: JsonValue | None, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be an array of strings",
        )
    return tuple(item for item in value if isinstance(item, str))


def _positive_int(value: JsonValue, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be a positive integer",
        )
    return value


def _json_object(value: JsonValue | None, field: str) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be an object",
        )
    return value
