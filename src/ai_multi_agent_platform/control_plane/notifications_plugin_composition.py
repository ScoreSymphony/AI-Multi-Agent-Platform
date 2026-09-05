"""Runtime-complete canonical Notification Control Plane composition.

Notifications are a built-in private attention domain, not a generic extension collection.
Their northbound routes remain explicit and hidden from generic extension discovery, while a
privacy-minimized derived projection participates in canonical global Search. Search never owns
Notification state and every result is re-authorized against the current recipient and source.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.notifications import Notification
from ai_multi_agent_platform.search import SearchDocument, SearchResult

from .authentication_hardening import (
    AuthenticatedControlPlaneHTTP as _BaseAuthenticatedControlPlaneHTTP,
)
from .extensions import (
    CommandHandler,
    ResourceService,
    _command_operation,
    _list_operation,
    _read_operation,
)
from .http import HTTPRequest, HTTPResponse, _header, _page_query, _request_context, _require_json
from .models import API_VERSION, APIException, api_exception_from_contract
from .notifications_composition import (
    NOTIFICATION_COLLECTION,
    NOTIFICATION_COMMANDS,
    NOTIFICATION_PREFERENCE_COLLECTION,
    _recipient_from_context,
)
from .notifications_live import ControlPlane as _NotificationControlPlane
from .notifications_live import ControlPlaneASGI
from .notifications_live import ControlPlaneHTTP as _NotificationControlPlaneHTTP
from .notifications_live import build_openapi as _build_notification_live_openapi

_NOTIFICATION_COLLECTIONS = frozenset({NOTIFICATION_COLLECTION, NOTIFICATION_PREFERENCE_COLLECTION})
_NOTIFICATION_COMMAND_SET = frozenset(NOTIFICATION_COMMANDS)
_NOTIFICATION_SEARCH_TYPE = "notification"


class ControlPlane(_NotificationControlPlane):
    """Public Control Plane with private Notifications hidden from generic extensions."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._notification_routes_locked = False
        super().__init__(*args, **kwargs)
        self._notification_routes_locked = True

    @property
    def registered_collections(self) -> tuple[str, ...]:
        return tuple(
            collection
            for collection in super().registered_collections
            if collection not in _NOTIFICATION_COLLECTIONS
        )

    @property
    def registered_commands(self) -> tuple[str, ...]:
        return tuple(
            command
            for command in super().registered_commands
            if command not in _NOTIFICATION_COMMAND_SET
        )

    async def _registered_extension_search_documents(
        self,
        correlation_id: str,
    ) -> tuple[list[SearchDocument], dict[str, tuple[str, str]]]:
        """Add private Notification metadata to derived Search without exposing generic routes."""

        documents, authorization = await super()._registered_extension_search_documents(
            correlation_id
        )
        if _NOTIFICATION_SEARCH_TYPE in authorization:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "notification Search type conflicts with another registered resource",
            )
        for notification in await self.notification_service.list_search_snapshot():
            documents.append(_notification_search_document(notification))
        authorization[_NOTIFICATION_SEARCH_TYPE] = (
            NOTIFICATION_COLLECTION,
            "notification:list",
        )
        return documents, authorization

    async def _search_result_allowed(
        self,
        context: Any,
        result: SearchResult,
    ) -> bool:
        if result.resource_type != _NOTIFICATION_SEARCH_TYPE:
            return await super()._search_result_allowed(context, result)

        try:
            recipient = _recipient_from_context(context)
        except ContractError:
            return False
        if result.owner_type != recipient.type.value or result.owner_id != recipient.id:
            return False
        if not self.notification_service.get_preference(recipient).in_app_enabled:
            return False

        try:
            notification = await self.notification_service.get(
                result.resource_id,
                recipient=recipient,
            )
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        if notification.state.value != result.status:
            return False
        if not await self.notification_source_visible(context, notification):
            return False
        return await self._allowed(
            context,
            "notification:list",
            NOTIFICATION_COLLECTION,
            owner_type=recipient.type.value,
            owner_id=recipient.id,
            project_id=notification.project_id,
        )

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if self._notification_routes_locked and collection in _NOTIFICATION_COLLECTIONS:
            raise ValueError(f"cannot override canonical notification collection: {collection}")
        super().register_resource_service(collection, service)

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if self._notification_routes_locked and command in _NOTIFICATION_COMMAND_SET:
            raise ValueError(f"cannot override canonical notification command: {command}")
        super().register_command(command, handler)


class ControlPlaneHTTP(_NotificationControlPlaneHTTP):
    """Publish canonical Notification routes without exposing them as generic extensions."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        segments = tuple(segment for segment in request.path.split("/") if segment)
        if len(segments) >= 2 and segments[:2] == ("api", API_VERSION):
            notification_response = await self._handle_notification_route(request, segments)
            if notification_response is not None:
                return notification_response

        response = await super().handle(request)
        if response.status != 200 or not isinstance(response.body, dict):
            return response

        if request.method == "GET" and request.path.rstrip("/") == f"/api/{API_VERSION}":
            body = dict(response.body)
            resources = _json_string_values(body.get("resources"))
            commands = _json_string_values(body.get("commands"))
            body["resources"] = _json_string_list(
                _append_unique(
                    resources,
                    (NOTIFICATION_COLLECTION, NOTIFICATION_PREFERENCE_COLLECTION),
                )
            )
            body["commands"] = _json_string_list(_append_unique(commands, NOTIFICATION_COMMANDS))
            return HTTPResponse(status=response.status, body=body, headers=dict(response.headers))

        if request.method == "GET" and request.path.rstrip("/") == (
            f"/api/{API_VERSION}/openapi.json"
        ):
            specification = _augment_notification_openapi(
                cast(dict[str, Any], deepcopy(response.body))
            )
            return HTTPResponse(
                status=response.status,
                body=cast(dict[str, JsonValue], specification),
                headers=dict(response.headers),
            )
        return response

    async def _handle_notification_route(
        self,
        request: HTTPRequest,
        segments: tuple[str, ...],
    ) -> HTTPResponse | None:
        request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
        correlation_id = _header(request.headers, "x-correlation-id") or request_id
        control_plane = cast(ControlPlane, self._control_plane)
        try:
            if len(segments) in {3, 4} and segments[2] in _NOTIFICATION_COLLECTIONS:
                if request.method != "GET":
                    raise APIException(
                        status=405,
                        code="method_not_allowed",
                        message="method not allowed",
                    )
                context = _request_context(request, request_id, correlation_id)
                if len(segments) == 3:
                    page = await control_plane.list_extension_resources(
                        context,
                        segments[2],
                        _page_query(request.query),
                    )
                    return self._response(200, page, request_id, correlation_id)
                item = await control_plane.get_extension_resource(
                    context,
                    segments[2],
                    segments[3],
                )
                return self._response(200, item, request_id, correlation_id)

            if (
                len(segments) == 4
                and segments[2] == "commands"
                and segments[3] in _NOTIFICATION_COMMAND_SET
            ):
                if request.method != "POST":
                    raise APIException(
                        status=405,
                        code="method_not_allowed",
                        message="method not allowed",
                    )
                _require_json(request)
                context = _request_context(request, request_id, correlation_id)
                resource_ref = request.body.get("resource_ref")
                if not isinstance(resource_ref, str) or not resource_ref.strip():
                    raise APIException(
                        status=400,
                        code="invalid_request",
                        message="resource_ref must be a non-blank string",
                        details={"field": "resource_ref"},
                    )
                payload = dict(request.body)
                payload.pop("resource_ref", None)
                result = await control_plane.execute_command(
                    context,
                    segments[3],
                    resource_ref,
                    payload,
                )
                return self._response(200, result, request_id, correlation_id)
        except ContractError as exc:
            return self._error_response(
                api_exception_from_contract(exc),
                request_id,
                correlation_id,
            )
        except APIException as exc:
            return self._error_response(exc, request_id, correlation_id)
        except (TypeError, ValueError) as exc:
            return self._error_response(
                APIException(status=400, code="invalid_request", message=str(exc)),
                request_id,
                correlation_id,
            )
        return None


class AuthenticatedControlPlaneHTTP(_BaseAuthenticatedControlPlaneHTTP):
    """Authenticate first, then delegate to the current Notification-aware HTTP surface."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._current_http = ControlPlaneHTTP(cast(ControlPlane, self._control_plane))


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build current OpenAPI with Notifications as canonical, not extension metadata."""

    specification = _build_notification_live_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
    )
    return _augment_notification_openapi(specification)


def _notification_search_document(notification: Notification) -> SearchDocument:
    """Build the intentionally small privacy-safe Search projection for one Notification."""

    keywords = tuple(
        dict.fromkeys(
            value
            for value in (
                "notification",
                notification.id,
                notification.category.value,
                notification.severity.value,
                notification.state.value,
                notification.source.resource_type,
                notification.source.resource_id,
                notification.task_id,
                notification.run_id,
                notification.approval_id,
                notification.verification_id,
                notification.node_id,
                notification.automation_id,
                notification.membership_id,
            )
            if value is not None
        )
    )
    tags = tuple(
        dict.fromkeys(
            (
                notification.category.value,
                notification.severity.value,
                notification.state.value,
                notification.source.resource_type,
            )
        )
    )
    return SearchDocument(
        resource_type=_NOTIFICATION_SEARCH_TYPE,
        resource_id=notification.id,
        title=(
            f"Notification: {notification.category.value} / {notification.severity.value}"
        ),
        summary="",
        project_id=notification.project_id,
        workspace_id=notification.workspace_id,
        owner_type=notification.recipient.type.value,
        owner_id=notification.recipient.id,
        status=notification.state.value,
        tags=tags,
        keywords=keywords,
        updated_at=notification.updated_at.isoformat(),
        canonical_ref=f"/api/{API_VERSION}/{NOTIFICATION_COLLECTION}/{notification.id}",
        provenance={"indexed_from": "canonical-notification-repository"},
    )


def _augment_notification_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    paths = specification.get("paths")
    if not isinstance(paths, dict):
        raise TypeError("OpenAPI paths must be an object")

    paths[f"/api/{API_VERSION}/{NOTIFICATION_COLLECTION}"] = {
        "get": _list_operation("listNotifications", "Recipient-scoped canonical notification page")
    }
    paths[f"/api/{API_VERSION}/{NOTIFICATION_COLLECTION}/{{resource_id}}"] = {
        "get": _read_operation("getNotification", "Recipient-scoped canonical notification")
    }
    paths[f"/api/{API_VERSION}/{NOTIFICATION_PREFERENCE_COLLECTION}"] = {
        "get": _list_operation(
            "listNotificationPreferences",
            "Current recipient notification preferences",
        )
    }
    paths[f"/api/{API_VERSION}/{NOTIFICATION_PREFERENCE_COLLECTION}/{{resource_id}}"] = {
        "get": _read_operation(
            "getNotificationPreference",
            "Current recipient notification preference",
        )
    }

    command_path = f"/api/{API_VERSION}/commands/{{command}}"
    command_entry = paths.get(command_path)
    existing_commands = _openapi_command_values(command_entry)
    combined_commands = _append_unique(existing_commands, NOTIFICATION_COMMANDS)
    if not isinstance(command_entry, dict) or not isinstance(command_entry.get("post"), dict):
        paths[command_path] = {
            "post": _command_operation("executeCanonicalCommand", combined_commands)
        }
    else:
        post = cast(dict[str, Any], command_entry["post"])
        parameters = post.get("parameters")
        if isinstance(parameters, list):
            for parameter in parameters:
                if not isinstance(parameter, dict) or parameter.get("name") != "command":
                    continue
                schema = parameter.get("schema")
                if isinstance(schema, dict):
                    schema["enum"] = list(combined_commands)
                    break

    components = specification.get("components")
    if isinstance(components, dict):
        schemas = components.get("schemas")
        if isinstance(schemas, dict) and "CanonicalExtensionCommandRequest" not in schemas:
            schemas["CanonicalExtensionCommandRequest"] = {
                "type": "object",
                "required": ["resource_ref"],
                "properties": {"resource_ref": {"type": "string", "minLength": 1}},
                "additionalProperties": True,
            }

    specification["x-notifications"] = {
        "collections": [NOTIFICATION_COLLECTION, NOTIFICATION_PREFERENCE_COLLECTION],
        "commands": list(NOTIFICATION_COMMANDS),
        "visibility": "recipient-scoped",
        "search_indexed": True,
        "search_projection": "privacy-minimized-derived-state",
        "source_of_truth": False,
    }
    return specification


def _openapi_command_values(command_entry: object) -> tuple[str, ...]:
    if not isinstance(command_entry, dict):
        return ()
    post = command_entry.get("post")
    if not isinstance(post, dict):
        return ()
    parameters = post.get("parameters")
    if not isinstance(parameters, list):
        return ()
    for parameter in parameters:
        if not isinstance(parameter, dict) or parameter.get("name") != "command":
            continue
        schema = parameter.get("schema")
        if not isinstance(schema, dict):
            return ()
        values = schema.get("enum")
        if not isinstance(values, list):
            return ()
        return tuple(value for value in values if isinstance(value, str))
    return ()


def _json_string_values(value: JsonValue | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _json_string_list(values: tuple[str, ...]) -> list[JsonValue]:
    result: list[JsonValue] = []
    for value in values:
        result.append(value)
    return result


def _append_unique(values: tuple[str, ...], additions: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*values, *additions)))


__all__ = [
    "AuthenticatedControlPlaneHTTP",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "build_openapi",
]
