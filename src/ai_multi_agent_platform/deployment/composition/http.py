"""Northbound HTTP/ASGI composition for the single-node deployment."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.control_plane import AuthenticatedControlPlaneHTTP, ControlPlaneASGI

from ..config import SingleNodeConfig
from .control_plane import ControlPlaneBundle
from .security import SecurityBundle


@dataclass(frozen=True, slots=True)
class HttpBundle:
    """Authenticated HTTP façade and ASGI application."""

    http: AuthenticatedControlPlaneHTTP
    app: ControlPlaneASGI


def build_http(
    config: SingleNodeConfig,
    security: SecurityBundle,
    control_plane: ControlPlaneBundle,
) -> HttpBundle:
    """Build northbound adapters only after Control Plane assembly is complete."""

    http = AuthenticatedControlPlaneHTTP(
        control_plane.control_plane,
        security.authentication,
        secure_cookie=config.secure_cookie,
    )
    return HttpBundle(http=http, app=ControlPlaneASGI(http))
