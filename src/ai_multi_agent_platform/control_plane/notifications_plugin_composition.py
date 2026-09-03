"""Compose notifications on top of the current Automation + plugin + terminal Control Plane."""

from __future__ import annotations

from typing import Any

from .automation_runtime_composition import (
    ControlPlane as _AutomationRuntimeControlPlane,
)
from .automation_runtime_composition import (
    ControlPlaneASGI as _AutomationRuntimeControlPlaneASGI,
)
from .notifications_live import ControlPlane as _NotificationControlPlane
from .notifications_live import ControlPlaneASGI as _NotificationControlPlaneASGI
from .notifications_live import ControlPlaneHTTP, build_openapi


class ControlPlane(_NotificationControlPlane, _AutomationRuntimeControlPlane):
    """Current Control Plane with notifications above the full Automation/runtime stack."""


class ControlPlaneASGI(_NotificationControlPlaneASGI):
    """Notification SSE outside the runtime-complete ASGI lifespan composition."""

    def __init__(self, http: Any) -> None:
        self._http = http
        self._inner = _AutomationRuntimeControlPlaneASGI(http)


__all__ = ["ControlPlane", "ControlPlaneASGI", "ControlPlaneHTTP", "build_openapi"]
