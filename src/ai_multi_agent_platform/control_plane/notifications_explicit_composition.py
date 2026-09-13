"""Explicit ownership boundary for the completed canonical Notification stack (#982).

Notification persistence, runtime projection, source integration and authorization are
one domain implementation refined through the existing single-inheritance chain. This
composition intercepts that domain's historical direct registrations while the chain is
constructed and publishes the final resource/command adapters exactly once as a named
ControlPlaneModule. Independent platform domains therefore no longer observe
Notification ownership as anonymous MRO side effects.
"""

from __future__ import annotations

from typing import Any

from .extensions import (
    CommandHandler,
    ControlPlaneModule,
    ControlPlaneRoute,
    ResourceService,
)
from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION
from .module_registry import install_control_plane_modules
from .notifications_composition import (
    NOTIFICATION_COLLECTION,
    NOTIFICATION_COMMANDS,
    NOTIFICATION_PREFERENCE_COLLECTION,
)
from .notifications_plugin_composition import (
    AuthenticatedControlPlaneHTTP,
    ControlPlaneASGI,
    ControlPlaneHTTP,
    build_openapi,
)
from .notifications_plugin_composition import (
    ControlPlane as _NotificationImplementationControlPlane,
)

NOTIFICATION_MODULE = "notifications"
_NOTIFICATION_COLLECTIONS = frozenset({NOTIFICATION_COLLECTION, NOTIFICATION_PREFERENCE_COLLECTION})
_NOTIFICATION_COMMANDS = frozenset(NOTIFICATION_COMMANDS)
_NOTIFICATION_STREAM_PATH = f"/api/{API_VERSION}/notifications/stream"


async def _notification_stream_transport_route(request: HTTPRequest) -> HTTPResponse:
    """Declare ownership for the SSE-only route on generic HTTP transports.

    The canonical Notification HTTP façade still performs the richer error-envelope
    mapping before generic route dispatch. This fallback exists so the registered
    special route remains executable and inspectably owned even when a lower-level
    generic HTTP façade is embedded directly.
    """

    del request
    return HTTPResponse(
        status=406,
        body={
            "error": {
                "code": "stream_transport_required",
                "message": "use the SSE transport for this endpoint",
            }
        },
        headers={},
    )


class ControlPlane(_NotificationImplementationControlPlane):
    """Completed Notification implementation with one explicit northbound owner."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._capturing_notification_composition = True
        self._captured_notification_resources: dict[str, ResourceService] = {}
        self._captured_notification_commands: dict[str, CommandHandler] = {}
        super().__init__(*args, **kwargs)
        self._capturing_notification_composition = False

        if frozenset(self._captured_notification_resources) != _NOTIFICATION_COLLECTIONS:
            raise RuntimeError("explicit Notification module resource inventory is incomplete")
        if frozenset(self._captured_notification_commands) != _NOTIFICATION_COMMANDS:
            raise RuntimeError("explicit Notification module command inventory is incomplete")

        install_control_plane_modules(
            self,
            (
                ControlPlaneModule(
                    name=NOTIFICATION_MODULE,
                    resource_services=dict(self._captured_notification_resources),
                    command_handlers=dict(self._captured_notification_commands),
                    routes=(
                        ControlPlaneRoute(
                            method="GET",
                            path=_NOTIFICATION_STREAM_PATH,
                            handler=_notification_stream_transport_route,
                        ),
                    ),
                ),
            ),
        )

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if self._capturing_notification_composition and collection in _NOTIFICATION_COLLECTIONS:
            self._captured_notification_resources[collection] = service
            return
        super().register_resource_service(collection, service)

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if self._capturing_notification_composition and command in _NOTIFICATION_COMMANDS:
            self._captured_notification_commands[command] = handler
            return
        super().register_command(command, handler)


__all__ = [
    "AuthenticatedControlPlaneHTTP",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "NOTIFICATION_MODULE",
    "build_openapi",
]
