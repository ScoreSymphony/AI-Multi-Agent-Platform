"""Canonical Control Plane surface for frontend presentation preferences."""

from __future__ import annotations

import json
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import (
    CommandHandler,
    ControlPlane,
    ControlPlaneModule,
    ResourceService,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.control_plane.module_registry import install_control_plane_modules

from .models import FRONTEND_CUSTOMIZATION_SCHEMA_VERSION, FrontendPreference
from .service import FrontendPreferenceService

FRONTEND_PREFERENCE_COLLECTION = "frontend-preferences"
FRONTEND_PREFERENCE_MODULE = "frontend-preferences"
FRONTEND_PREFERENCE_COMMANDS = (
    "frontend-preference.update",
    "frontend-preference.reset",
)
_MAX_CUSTOMIZATION_BYTES = 512 * 1024
_ALLOWED_CUSTOMIZATION_KEYS = {
    "version",
    "appearance",
    "navigation",
    "branding",
    "layout",
    "dashboard",
}


class FrontendPreferenceResourceService(ResourceService):
    def __init__(self, service: FrontendPreferenceService) -> None:
        self._service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        principal_ref = _principal_ref(context)
        return (_resource(await self._service.get(principal_ref)),)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        principal_ref = _principal_ref(context)
        _require_owned_resource(principal_ref, resource_id)
        return _resource(await self._service.get(principal_ref))


def frontend_preference_control_plane_module(
    service: FrontendPreferenceService,
) -> ControlPlaneModule:
    handlers: dict[str, CommandHandler] = {
        "frontend-preference.update": _update_handler(service),
        "frontend-preference.reset": _reset_handler(service),
    }
    return ControlPlaneModule(
        name=FRONTEND_PREFERENCE_MODULE,
        resource_services={
            FRONTEND_PREFERENCE_COLLECTION: FrontendPreferenceResourceService(service),
        },
        command_handlers=handlers,
    )


def register_frontend_preference_control_plane(
    control_plane: ControlPlane,
    service: FrontendPreferenceService,
) -> None:
    install_control_plane_modules(
        control_plane,
        (frontend_preference_control_plane_module(service),),
    )


def _update_handler(service: FrontendPreferenceService) -> CommandHandler:
    async def update(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        principal_ref = _principal_ref(context)
        _require_owned_resource(principal_ref, resource_ref)
        allowed = {"schema_version", "customization", "expected_revision"}
        unexpected = sorted(set(payload).difference(allowed))
        if unexpected:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "frontend preference update contains unknown fields",
                details={"fields": cast(JsonValue, unexpected)},
            )
        schema_version = payload.get("schema_version")
        if schema_version != FRONTEND_CUSTOMIZATION_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "unsupported frontend customization schema version",
            )
        customization = _customization(payload.get("customization"))
        expected_revision = _expected_revision(payload.get("expected_revision"))
        saved = await service.update(
            principal_ref,
            customization,
            expected_revision=expected_revision,
        )
        return _resource(saved)

    return update


def _reset_handler(service: FrontendPreferenceService) -> CommandHandler:
    async def reset(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        principal_ref = _principal_ref(context)
        _require_owned_resource(principal_ref, resource_ref)
        unexpected = sorted(set(payload).difference({"expected_revision"}))
        if unexpected:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "frontend preference reset contains unknown fields",
                details={"fields": cast(JsonValue, unexpected)},
            )
        reset_preference = await service.reset(
            principal_ref,
            expected_revision=_expected_revision(payload.get("expected_revision")),
        )
        return _resource(reset_preference)

    return reset


def _principal_ref(context: RequestContext) -> str:
    principal_ref = context.actor.principal_ref.strip()
    if not principal_ref or principal_ref == "local:anonymous":
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "frontend preferences require an authenticated principal",
        )
    return principal_ref


def _require_owned_resource(principal_ref: str, resource_ref: str) -> None:
    if resource_ref != principal_ref:
        raise ContractError(ErrorCode.NOT_FOUND, "frontend preference not found")


def _expected_revision(value: JsonValue | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "expected_revision must be a non-negative integer",
        )
    return value


def _customization(value: JsonValue | None) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "customization must be an object",
        )
    if value.get("version") != FRONTEND_CUSTOMIZATION_SCHEMA_VERSION:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "customization version does not match schema_version",
        )
    unexpected = sorted(set(value).difference(_ALLOWED_CUSTOMIZATION_KEYS))
    if unexpected:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "customization contains unsupported top-level fields",
            details={"fields": cast(JsonValue, unexpected)},
        )
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "customization must contain JSON values only",
        ) from exc
    if len(encoded) > _MAX_CUSTOMIZATION_BYTES:
        raise ContractError(
            ErrorCode.INPUT_TOO_LARGE,
            "frontend customization exceeds the supported size limit",
        )
    return dict(value)


def _resource(preference: FrontendPreference) -> dict[str, JsonValue]:
    return {
        "id": preference.principal_ref,
        "type": "frontend-preference",
        "scope": "user",
        "schema_version": preference.schema_version,
        "revision": preference.revision,
        "updated_at": None if preference.updated_at is None else preference.updated_at.isoformat(),
        "customization": (
            None if preference.customization is None else dict(preference.customization)
        ),
    }


__all__ = [
    "FRONTEND_PREFERENCE_COLLECTION",
    "FRONTEND_PREFERENCE_COMMANDS",
    "FRONTEND_PREFERENCE_MODULE",
    "FrontendPreferenceResourceService",
    "frontend_preference_control_plane_module",
    "register_frontend_preference_control_plane",
]
