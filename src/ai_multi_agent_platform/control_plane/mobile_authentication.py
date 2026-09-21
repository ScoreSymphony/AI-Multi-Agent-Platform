"""Mobile pairing HTTP helpers kept separate from the core authentication transport."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security.authentication import (
    AuthenticatedActor,
    IssuedCredential,
    MobilePairingGrant,
)

from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION


def handle_public_mobile_auth_route(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    *,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if request.method != "POST" or relative != "/auth/mobile-pairings:consume":
        return None
    pairing_id = _optional_string(request.body.get("pairing_id"), "pairing_id")
    platform = _optional_string(request.body.get("platform"), "platform")
    device, issued = owner._authentication.mobile_pairing.consume_challenge(
        pairing_id,
        _required_string(request.body, "code"),
        device_name=_required_string(request.body, "device_name"),
        platform=platform,
        metadata=_optional_object(request.body.get("metadata"), "metadata"),
        protocol_version=_required_string(request.body, "protocol_version"),
        caller_ref=request.transport_peer,
        correlation_id=correlation_id,
    )
    return _typed_response(
        owner,
        201,
        {
            "device": owner._authentication.mobile_pairing.safe_device(device),
            "credential": _issued_credential(issued),
        },
        request_id,
        correlation_id,
    )


async def handle_public_mobile_auth_route_async(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    *,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if request.method != "POST" or relative != "/auth/mobile-pairings:consume":
        return None
    pairing_id = _optional_string(request.body.get("pairing_id"), "pairing_id")
    device, issued = await owner._runtime_authentication.consume_mobile_pairing(
        pairing_id,
        _required_string(request.body, "code"),
        device_name=_required_string(request.body, "device_name"),
        platform=_optional_string(request.body.get("platform"), "platform"),
        metadata=_optional_object(request.body.get("metadata"), "metadata"),
        protocol_version=_required_string(request.body, "protocol_version"),
        caller_ref=request.transport_peer,
        correlation_id=correlation_id,
    )
    return _typed_response(
        owner,
        201,
        {
            "device": await owner._runtime_authentication.safe_mobile_device(device),
            "credential": _issued_credential(issued),
        },
        request_id,
        correlation_id,
    )


async def handle_authenticated_mobile_auth_route(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    actor: AuthenticatedActor,
    *,
    user_id: str,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if relative.startswith("/auth/mobile-pairings"):
        return await _handle_pairing_management(
            owner,
            request,
            relative,
            actor,
            user_id=user_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )
    if relative.startswith("/auth/mobile-devices"):
        return await _handle_device_management(
            owner,
            request,
            relative,
            actor,
            user_id=user_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )
    return None


async def handle_authenticated_mobile_auth_route_async(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    actor: AuthenticatedActor,
    *,
    user_id: str,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if relative.startswith("/auth/mobile-pairings"):
        return await _handle_pairing_management_async(
            owner,
            request,
            relative,
            actor,
            user_id=user_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )
    if relative.startswith("/auth/mobile-devices"):
        return await _handle_device_management_async(
            owner,
            request,
            relative,
            actor,
            user_id=user_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )
    return None


async def _handle_pairing_management(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    actor: AuthenticatedActor,
    *,
    user_id: str,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if request.method == "POST" and relative == "/auth/mobile-pairings":
        await _authorize(
            owner,
            request,
            actor,
            action="create",
            resource_ref=user_id,
            request_id=request_id,
            correlation_id=correlation_id,
            bind_payload=True,
        )
        grant = owner._authentication.mobile_pairing.create_challenge(
            user_id,
            _required_string(request.body, "server_origin"),
            correlation_id=correlation_id,
        )
        return _typed_response(owner, 201, _pairing_grant(grant), request_id, correlation_id)

    if request.method == "POST" and relative.startswith("/auth/mobile-pairings/"):
        pairing_id = relative.removeprefix("/auth/mobile-pairings/").removesuffix(":cancel")
        if not relative.endswith(":cancel"):
            return None
        await _authorize(
            owner,
            request,
            actor,
            action="revoke",
            resource_ref=pairing_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        owner._authentication.mobile_pairing.cancel_challenge(
            user_id,
            pairing_id,
            correlation_id=correlation_id,
        )
        return _typed_response(
            owner,
            200,
            {"id": pairing_id, "cancelled": True},
            request_id,
            correlation_id,
        )
    return None


async def _handle_pairing_management_async(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    actor: AuthenticatedActor,
    *,
    user_id: str,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if request.method == "POST" and relative == "/auth/mobile-pairings":
        await _authorize(
            owner,
            request,
            actor,
            action="create",
            resource_ref=user_id,
            request_id=request_id,
            correlation_id=correlation_id,
            bind_payload=True,
        )
        grant = await owner._runtime_authentication.create_mobile_pairing(
            user_id,
            _required_string(request.body, "server_origin"),
            correlation_id=correlation_id,
        )
        return _typed_response(owner, 201, _pairing_grant(grant), request_id, correlation_id)

    if request.method == "POST" and relative.startswith("/auth/mobile-pairings/"):
        pairing_id = relative.removeprefix("/auth/mobile-pairings/").removesuffix(":cancel")
        if not relative.endswith(":cancel"):
            return None
        await _authorize(
            owner,
            request,
            actor,
            action="revoke",
            resource_ref=pairing_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        await owner._runtime_authentication.cancel_mobile_pairing(
            user_id,
            pairing_id,
            correlation_id=correlation_id,
        )
        return _typed_response(
            owner,
            200,
            {"id": pairing_id, "cancelled": True},
            request_id,
            correlation_id,
        )
    return None


async def _handle_device_management(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    actor: AuthenticatedActor,
    *,
    user_id: str,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if request.method == "GET" and relative == "/auth/mobile-devices":
        await _authorize_device_action(
            owner,
            request,
            actor,
            "list",
            user_id,
            request_id,
            correlation_id,
        )
        items: list[JsonValue] = [
            owner._authentication.mobile_pairing.safe_device(device)
            for device in owner._authentication.mobile_pairing.list_devices(user_id)
        ]
        return _typed_response(owner, 200, {"items": items}, request_id, correlation_id)

    if request.method == "POST" and relative == "/auth/mobile-devices:revoke-all":
        await _authorize_device_action(
            owner,
            request,
            actor,
            "revoke",
            user_id,
            request_id,
            correlation_id,
        )
        count = owner._authentication.mobile_pairing.revoke_all_devices(
            user_id,
            correlation_id=correlation_id,
        )
        return _typed_response(owner, 200, {"revoked": count}, request_id, correlation_id)

    return await _handle_one_device(
        owner,
        request,
        relative,
        actor,
        user_id=user_id,
        request_id=request_id,
        correlation_id=correlation_id,
    )


async def _handle_device_management_async(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    actor: AuthenticatedActor,
    *,
    user_id: str,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if request.method == "GET" and relative == "/auth/mobile-devices":
        await _authorize_device_action(
            owner,
            request,
            actor,
            "list",
            user_id,
            request_id,
            correlation_id,
        )
        devices = await owner._runtime_authentication.list_mobile_devices(user_id)
        items: list[JsonValue] = [
            await owner._runtime_authentication.safe_mobile_device(device) for device in devices
        ]
        return _typed_response(owner, 200, {"items": items}, request_id, correlation_id)

    if request.method == "POST" and relative == "/auth/mobile-devices:revoke-all":
        await _authorize_device_action(
            owner,
            request,
            actor,
            "revoke",
            user_id,
            request_id,
            correlation_id,
        )
        count = await owner._runtime_authentication.revoke_all_mobile_devices(
            user_id,
            correlation_id=correlation_id,
        )
        return _typed_response(owner, 200, {"revoked": count}, request_id, correlation_id)

    return await _handle_one_device_async(
        owner,
        request,
        relative,
        actor,
        user_id=user_id,
        request_id=request_id,
        correlation_id=correlation_id,
    )


async def _handle_one_device(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    actor: AuthenticatedActor,
    *,
    user_id: str,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if request.method != "POST" or not relative.startswith("/auth/mobile-devices/"):
        return None
    suffix = relative.removeprefix("/auth/mobile-devices/")
    if suffix.endswith(":rename"):
        device_id = suffix.removesuffix(":rename")
        await _authorize(
            owner,
            request,
            actor,
            action="update",
            resource_ref=device_id,
            request_id=request_id,
            correlation_id=correlation_id,
            bind_payload=True,
        )
        device = owner._authentication.mobile_pairing.rename_device(
            user_id,
            device_id,
            _required_string(request.body, "display_name"),
            correlation_id=correlation_id,
        )
        return _typed_response(
            owner,
            200,
            owner._authentication.mobile_pairing.safe_device(device),
            request_id,
            correlation_id,
        )
    if suffix.endswith(":revoke"):
        device_id = suffix.removesuffix(":revoke")
        await _authorize_device_action(
            owner,
            request,
            actor,
            "revoke",
            device_id,
            request_id,
            correlation_id,
        )
        owner._authentication.mobile_pairing.revoke_device(
            user_id,
            device_id,
            correlation_id=correlation_id,
        )
        return _typed_response(
            owner,
            200,
            {"id": device_id, "revoked": True},
            request_id,
            correlation_id,
        )
    return None


async def _handle_one_device_async(
    owner: Any,
    request: HTTPRequest,
    relative: str,
    actor: AuthenticatedActor,
    *,
    user_id: str,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse | None:
    if request.method != "POST" or not relative.startswith("/auth/mobile-devices/"):
        return None
    suffix = relative.removeprefix("/auth/mobile-devices/")
    if suffix.endswith(":rename"):
        device_id = suffix.removesuffix(":rename")
        await _authorize(
            owner,
            request,
            actor,
            action="update",
            resource_ref=device_id,
            request_id=request_id,
            correlation_id=correlation_id,
            bind_payload=True,
        )
        device = await owner._runtime_authentication.rename_mobile_device(
            user_id,
            device_id,
            _required_string(request.body, "display_name"),
            correlation_id=correlation_id,
        )
        return _typed_response(
            owner,
            200,
            await owner._runtime_authentication.safe_mobile_device(device),
            request_id,
            correlation_id,
        )
    if suffix.endswith(":revoke"):
        device_id = suffix.removesuffix(":revoke")
        await _authorize_device_action(
            owner,
            request,
            actor,
            "revoke",
            device_id,
            request_id,
            correlation_id,
        )
        await owner._runtime_authentication.revoke_mobile_device(
            user_id,
            device_id,
            correlation_id=correlation_id,
        )
        return _typed_response(
            owner,
            200,
            {"id": device_id, "revoked": True},
            request_id,
            correlation_id,
        )
    return None


async def _authorize_device_action(
    owner: Any,
    request: HTTPRequest,
    actor: AuthenticatedActor,
    action: str,
    resource_ref: str,
    request_id: str,
    correlation_id: str,
) -> None:
    await _authorize(
        owner,
        request,
        actor,
        action=action,
        resource_ref=resource_ref,
        request_id=request_id,
        correlation_id=correlation_id,
    )


def mobile_auth_openapi_paths(csrf_parameter: dict[str, Any]) -> dict[str, Any]:
    path_parameter = {
        "name": "device_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }
    return {
        f"/api/{API_VERSION}/auth/mobile-pairings:consume": {
            "post": _operation(
                "consumeMobilePairing",
                "Consume a short-lived single-use mobile pairing proof and issue one "
                "device credential.",
                public=True,
                request_fields=("code", "device_name", "protocol_version"),
                status="201",
            )
        },
        f"/api/{API_VERSION}/auth/mobile-pairings": {
            "post": _operation(
                "createMobilePairing",
                "Create a short-lived mobile pairing challenge after manage-credentials "
                "authorization.",
                request_fields=("server_origin",),
                status="201",
                parameters=(csrf_parameter,),
            )
        },
        f"/api/{API_VERSION}/auth/mobile-pairings/{{pairing_id}}:cancel": {
            "post": _operation(
                "cancelMobilePairing",
                "Cancel an unused mobile pairing challenge owned by the current user.",
                error_statuses=("404",),
                parameters=(
                    {
                        "name": "pairing_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    csrf_parameter,
                ),
            )
        },
        f"/api/{API_VERSION}/auth/mobile-devices": {
            "get": _operation(
                "listMobileDevices",
                "List paired mobile devices without credential secrets.",
            )
        },
        f"/api/{API_VERSION}/auth/mobile-devices:revoke-all": {
            "post": _operation(
                "revokeAllMobileDevices",
                "Revoke all active mobile device credentials owned by the current user.",
                parameters=(csrf_parameter,),
            )
        },
        f"/api/{API_VERSION}/auth/mobile-devices/{{device_id}}:rename": {
            "post": _operation(
                "renameMobileDevice",
                "Rename paired-device display metadata.",
                request_fields=("display_name",),
                error_statuses=("404",),
                parameters=(path_parameter, csrf_parameter),
            )
        },
        f"/api/{API_VERSION}/auth/mobile-devices/{{device_id}}:revoke": {
            "post": _operation(
                "revokeMobileDevice",
                "Revoke one paired mobile device credential.",
                error_statuses=("404",),
                parameters=(path_parameter, csrf_parameter),
            )
        },
    }


def _typed_response(
    owner: Any,
    status: int,
    body: Any,
    request_id: str,
    correlation_id: str,
) -> HTTPResponse:
    responder = cast(
        Callable[[int, Any, str, str], HTTPResponse],
        owner._response,
    )
    return responder(status, body, request_id, correlation_id)


async def _authorize(
    owner: Any,
    request: HTTPRequest,
    actor: AuthenticatedActor,
    *,
    action: str,
    resource_ref: str,
    request_id: str,
    correlation_id: str,
    bind_payload: bool = False,
) -> None:
    await owner._authorize_credential_operation(
        request,
        actor,
        action=action,
        resource_ref=resource_ref,
        request_id=request_id,
        correlation_id=correlation_id,
        bind_payload=bind_payload,
    )


def _pairing_grant(grant: MobilePairingGrant) -> dict[str, JsonValue]:
    return {
        "id": grant.pairing_id,
        "server_origin": grant.server_origin,
        "code": grant.secret,
        "pairing_uri": grant.pairing_uri,
        "protocol_version": grant.protocol_version,
        "expires_at": grant.expires_at.isoformat(),
        "secret_display": "one_time",
    }


def _issued_credential(issued: IssuedCredential) -> dict[str, JsonValue]:
    return {
        "id": issued.credential_id,
        "secret": issued.secret,
        "expires_at": issued.expires_at.isoformat() if issued.expires_at else None,
        "secret_display": "one_time",
    }


def _required_string(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_string(value: JsonValue | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_object(value: JsonValue | None, name: str) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _operation(
    operation_id: str,
    description: str,
    *,
    public: bool = False,
    request_fields: tuple[str, ...] = (),
    status: str = "200",
    error_statuses: tuple[str, ...] = (),
    parameters: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    responses: dict[str, Any] = {
        status: {"description": "Authentication operation result"},
        "400": {"$ref": "#/components/responses/Error"},
        "401": {"$ref": "#/components/responses/Error"},
        "403": {"$ref": "#/components/responses/Error"},
        "429": {"$ref": "#/components/responses/Error"},
    }
    for error_status in error_statuses:
        responses.setdefault(error_status, {"$ref": "#/components/responses/Error"})

    operation: dict[str, Any] = {
        "operationId": operation_id,
        "description": description,
        "responses": responses,
    }
    if public:
        operation["security"] = []
    if parameters:
        operation["parameters"] = list(parameters)
    if request_fields:
        operation["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {field: {"type": "string"} for field in request_fields},
                        "required": list(request_fields),
                        "additionalProperties": True,
                    }
                }
            },
        }
    return operation


__all__ = [
    "handle_authenticated_mobile_auth_route",
    "handle_authenticated_mobile_auth_route_async",
    "handle_public_mobile_auth_route",
    "handle_public_mobile_auth_route_async",
    "mobile_auth_openapi_paths",
]
