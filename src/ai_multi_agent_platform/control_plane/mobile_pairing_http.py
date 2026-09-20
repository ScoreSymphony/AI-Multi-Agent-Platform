"""Mobile pairing HTTP parsing, projection and OpenAPI helpers.

The canonical Authentication service owns lifecycle state. This module only keeps the
northbound mobile-pairing transport contract out of the general authentication router.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security.authentication import MobileDeviceGrant, StoredCredential
from ai_multi_agent_platform.security.authentication_serialization import safe_mobile_device

from .models import APIException


@dataclass(frozen=True, slots=True)
class MobilePairingConsumeInput:
    pairing_code: str
    server_origin: str
    device_name: str
    device_platform: str
    pairing_id: str | None
    protocol_version: int


def parse_mobile_pairing_consume(
    payload: dict[str, JsonValue],
) -> MobilePairingConsumeInput:
    _require_only_fields(
        payload,
        {
            "pairing_code",
            "pairing_id",
            "server_origin",
            "device_name",
            "device_platform",
            "protocol_version",
        },
    )
    return MobilePairingConsumeInput(
        pairing_code=_required_string(payload, "pairing_code"),
        server_origin=_required_string(payload, "server_origin"),
        device_name=_required_string(payload, "device_name"),
        device_platform=_required_string(payload, "device_platform"),
        pairing_id=_optional_string(payload.get("pairing_id")),
        protocol_version=_protocol_version(payload.get("protocol_version")),
    )


def mobile_device_grant_body(
    grant: MobileDeviceGrant,
    credential: StoredCredential | None,
) -> dict[str, JsonValue]:
    return {
        "device": safe_mobile_device(grant.device, credential),
        "credential": {
            "id": grant.credential.credential_id,
            "secret": grant.credential.secret,
            "expires_at": (
                grant.credential.expires_at.isoformat()
                if grant.credential.expires_at is not None
                else None
            ),
            "secret_display": "one_time",
        },
    }


def mobile_pairing_openapi_paths(
    *,
    api_version: str,
    csrf_parameter: dict[str, Any],
    operation_factory: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    paths = _pairing_openapi_paths(
        api_version=api_version,
        csrf_parameter=csrf_parameter,
        operation_factory=operation_factory,
    )
    paths.update(
        _device_openapi_paths(
            api_version=api_version,
            csrf_parameter=csrf_parameter,
            operation_factory=operation_factory,
        )
    )
    return paths


def _pairing_openapi_paths(
    *,
    api_version: str,
    csrf_parameter: dict[str, Any],
    operation_factory: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    consume = operation_factory(
        "consumeMobilePairing",
        "Consume a short-lived mobile pairing challenge and issue one device credential.",
        public=True,
        status="201",
    )
    consume["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "pairing_code": {"type": "string"},
                        "pairing_id": {"type": "string"},
                        "server_origin": {"type": "string", "format": "uri"},
                        "device_name": {"type": "string"},
                        "device_platform": {
                            "type": "string",
                            "enum": ["android", "ios"],
                        },
                        "protocol_version": {"type": "integer", "enum": [1]},
                    },
                    "required": [
                        "pairing_code",
                        "server_origin",
                        "device_name",
                        "device_platform",
                    ],
                    "additionalProperties": False,
                }
            }
        },
    }
    return {
        f"/api/{api_version}/auth/mobile-pairings": {
            "post": operation_factory(
                "createMobilePairing",
                "Create a short-lived single-use mobile pairing challenge.",
                request_fields=("server_origin",),
                status="201",
                parameters=(csrf_parameter,),
            )
        },
        f"/api/{api_version}/auth/mobile-pairings:consume": {"post": consume},
        f"/api/{api_version}/auth/mobile-pairings/{{pairing_id}}:cancel": {
            "post": operation_factory(
                "cancelMobilePairing",
                "Cancel an unused pairing challenge owned by the current user.",
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
    }


def _device_openapi_paths(
    *,
    api_version: str,
    csrf_parameter: dict[str, Any],
    operation_factory: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    return {
        f"/api/{api_version}/auth/mobile-devices": {
            "get": operation_factory(
                "listMobileDevices",
                "List safe paired-device metadata for the current user.",
            )
        },
        f"/api/{api_version}/auth/mobile-devices/{{device_id}}:revoke": {
            "post": operation_factory(
                "revokeMobileDevice",
                "Revoke one paired mobile device credential.",
                parameters=(
                    {
                        "name": "device_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    csrf_parameter,
                ),
            )
        },
        f"/api/{api_version}/auth/mobile-devices:revoke-all": {
            "post": operation_factory(
                "revokeAllMobileDevices",
                "Revoke all paired mobile device credentials for the current user.",
                parameters=(csrf_parameter,),
            )
        },
    }


def _require_only_fields(payload: dict[str, JsonValue], allowed: set[str]) -> None:
    unexpected = set(payload).difference(allowed)
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise APIException(
            status=400,
            code="invalid_request",
            message=f"unexpected request field(s): {names}",
        )


def _required_string(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("optional string value must be non-empty when provided")
    return value.strip()


def _protocol_version(value: JsonValue | None) -> int:
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, int):
        raise APIException(
            status=400,
            code="invalid_request",
            message="protocol_version must be an integer",
        )
    if value != 1:
        raise APIException(
            status=400,
            code="invalid_request",
            message="unsupported mobile pairing protocol version",
        )
    return value


__all__ = [
    "MobilePairingConsumeInput",
    "mobile_device_grant_body",
    "mobile_pairing_openapi_paths",
    "parse_mobile_pairing_consume",
]
