"""Read-only release/update status on the current Control Plane surface."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .extensions import ControlPlaneModule, ControlPlaneRoute
from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION
from .module_registry import install_control_plane_modules
from .task_project_reassignment import (
    AuthenticatedControlPlaneHTTP as _CurrentAuthenticatedControlPlaneHTTP,
)
from .task_project_reassignment import ControlPlane, ControlPlaneASGI
from .task_project_reassignment import ControlPlaneHTTP as _CurrentControlPlaneHTTP
from .task_project_reassignment import build_openapi as _build_current_openapi

if TYPE_CHECKING:
    from ai_multi_agent_platform.release.operator import ReleaseOperatorService

RELEASE_STATUS_PATH = f"/api/{API_VERSION}/release/status"
RELEASE_STATUS_MODULE = "release-status"


def _runtime_release_operator() -> ReleaseOperatorService:
    from ai_multi_agent_platform.release.operator import ReleaseOperatorService

    return ReleaseOperatorService.runtime_defaults()


def _release_status_module(operator: ReleaseOperatorService) -> ControlPlaneModule:
    async def release_status(request: HTTPRequest) -> HTTPResponse:
        del request
        return HTTPResponse(
            status=200,
            body=cast(JsonValue, operator.status()),
            headers={},
        )

    return ControlPlaneModule(
        name=RELEASE_STATUS_MODULE,
        routes=(
            ControlPlaneRoute(
                method="GET",
                path=RELEASE_STATUS_PATH,
                handler=release_status,
            ),
        ),
        openapi_contributors=(_augment_openapi,),
    )


def _install_release_status_module(
    control_plane: Any,
    release_operator: ReleaseOperatorService | None,
) -> ReleaseOperatorService:
    existing = getattr(control_plane, "_release_operator", None)
    if existing is not None:
        if release_operator is not None and release_operator is not existing:
            raise ValueError("release status operator is already bound to this Control Plane")
        return cast("ReleaseOperatorService", existing)

    operator = release_operator or _runtime_release_operator()
    registered_modules = getattr(control_plane, "registered_modules", ())
    if RELEASE_STATUS_MODULE not in registered_modules and hasattr(
        control_plane, "register_modules"
    ):
        install_control_plane_modules(control_plane, (_release_status_module(operator),))
    setattr(control_plane, "_release_operator", operator)
    return operator


class ControlPlaneHTTP(_CurrentControlPlaneHTTP):
    """Expose release metadata through an explicitly owned special route."""

    def __init__(
        self,
        control_plane: Any,
        *,
        release_operator: ReleaseOperatorService | None = None,
    ) -> None:
        super().__init__(control_plane)
        self._release_operator = _install_release_status_module(
            control_plane,
            release_operator,
        )

    @property
    def release_operator(self) -> ReleaseOperatorService:
        return self._release_operator

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            body = deepcopy(response.body)
            body["release_status"] = RELEASE_STATUS_PATH
            return HTTPResponse(
                status=response.status,
                body=body,
                headers=dict(response.headers),
            )
        return response


class AuthenticatedControlPlaneHTTP(_CurrentAuthenticatedControlPlaneHTTP):
    """Authenticate normally, then expose the explicitly owned #42 status route."""

    def __init__(
        self,
        *args: Any,
        release_operator: ReleaseOperatorService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        release_status_http = ControlPlaneHTTP(
            self._control_plane,
            release_operator=release_operator,
        )
        self._release_status_http = release_status_http
        self._current_http = release_status_http

    @property
    def release_operator(self) -> ReleaseOperatorService:
        return self._release_status_http.release_operator


def _augment_openapi(specification: dict[str, Any]) -> None:
    paths = specification.get("paths")
    if isinstance(paths, dict):
        paths[RELEASE_STATUS_PATH] = {
            "get": {
                "operationId": "getReleaseStatus",
                "summary": "Read platform release, compatibility and upstream update status",
                "responses": {
                    "200": {"description": "Read-only release/update operator status"},
                    "401": {"description": "Authentication required"},
                },
            }
        }
    specification["x-release-update-policy"] = {
        "discovery": "advisory-only",
        "automatic_production_updates": False,
        "production_pin_mutation": "not_exposed_by_control_plane",
    }


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
    include_conversations: bool = False,
    include_approval_decisions: bool = False,
) -> dict[str, Any]:
    specification = _build_current_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
        include_conversations=include_conversations,
        include_approval_decisions=include_approval_decisions,
    )
    _augment_openapi(specification)
    return specification


__all__ = [
    "AuthenticatedControlPlaneHTTP",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "RELEASE_STATUS_MODULE",
    "RELEASE_STATUS_PATH",
    "build_openapi",
]
